"""spec/03 — 错误判定链（判定顺序双端一致，改动必须先改 spec）。"""

from __future__ import annotations

from dataclasses import dataclass

from grpc import StatusCode

ERR_GQL_PARSE_PREFIX = "ERR_GQL_PARSE:"
ERR_PERMISSION_PREFIX = "ERR_PERMISSION:"
ERR_NO_CONTEXT_PREFIX = "ERR_NO_CONTEXT:"

GUARD_CODES_INVALID = ("INVALID_PARAM", "INVALID_BODY")


@dataclass
class MappedError(Exception):
    """已映射的 gRPC 错误：status / details / 稳定码（trailer store-error-code）。"""

    status: StatusCode
    details: str
    code: str

    def __str__(self) -> str:  # abort 需要 message 可读
        return self.details or self.code


def store_code(err: BaseException) -> str:
    """store 错误的分类码：优先 err.code，缺省用类型名，再缺省通用码（与 REST storeCode 链一致）。"""
    return getattr(err, "code", None) or type(err).__name__ or "STORE_ERROR"


class _GuardError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def invalid_param(message: str) -> _GuardError:
    return _GuardError("INVALID_PARAM", message)


def invalid_body(message: str) -> _GuardError:
    return _GuardError("INVALID_BODY", message)


def not_found(message: str) -> _GuardError:
    return _GuardError("NOT_FOUND", message)


class ContextError(_GuardError):
    def __init__(self, message: str):
        super().__init__("CONTEXT_ERROR", message)


def parse_json_field(raw: str | None, make_err, label: str):
    """spec/02：空串/None ⇒ None；非法 JSON ⇒ 带码错误。"""
    if raw is None or raw == "":
        return None
    import json

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise make_err(f"{label} 不是合法 JSON: {e}") from e


def require_json_object(raw: str | None, label: str):
    """spec/02：必须为 JSON 对象编码（数组/标量/非法 JSON 拒绝）；空 ⇒ None。"""
    import json

    v = parse_json_field(raw, invalid_body, label)
    if v is not None and not isinstance(v, dict):
        raise invalid_body(f"{label} 必须是 JSON 对象编码")
    return v


def map_store_error(err: BaseException, permission_error: type[BaseException] | None) -> MappedError:
    """spec/03 判定链：适配层守卫 → PermissionError 类 → ERR_PERMISSION: 前缀 → ERR_NO_CONTEXT: 前缀 / code → ERR_GQL_PARSE: 前缀 → INTERNAL。

    details 一律原 message 透传、取不到置空串（禁伪造）。
    """
    message = str(getattr(err, "args", [None])[0]) if getattr(err, "args", None) else ""
    if message is None:
        message = ""

    def fail(status: StatusCode, code: str, details: str | None) -> MappedError:
        return MappedError(status, details if details is not None else "", code)

    if isinstance(err, _GuardError):
        if err.code == "NOT_FOUND":
            return fail(StatusCode.NOT_FOUND, err.code, err.args[0])
        if err.code == "CONTEXT_ERROR":
            return fail(StatusCode.UNAUTHENTICATED, err.code, err.args[0])
        return fail(StatusCode.INVALID_ARGUMENT, err.code, err.args[0])

    if permission_error is not None and isinstance(err, permission_error):
        return fail(StatusCode.PERMISSION_DENIED, store_code(err), message)

    if message.startswith(ERR_PERMISSION_PREFIX):
        return fail(StatusCode.PERMISSION_DENIED, "ERR_PERMISSION", message)

    # spec/03 判定顺序：权限类同档 —— NoContext（requireContext 开启且 ctx 缺失）。
    # py-store NoContextError 带 machine code `no_context`（已剥前缀）；字符串通道 host
    # 保留 `ERR_NO_CONTEXT:` 前缀。二者同 ⇒ PERMISSION_DENIED。
    if getattr(err, "code", None) == "no_context" or message.startswith(ERR_NO_CONTEXT_PREFIX):
        return fail(StatusCode.PERMISSION_DENIED, "no_context", message)

    if message.startswith(ERR_GQL_PARSE_PREFIX):
        return fail(StatusCode.INVALID_ARGUMENT, "GQL_PARSE", message[len(ERR_GQL_PARSE_PREFIX):])

    return fail(StatusCode.INTERNAL, store_code(err), message)
