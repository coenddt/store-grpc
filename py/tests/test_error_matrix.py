"""映射矩阵：每档错误语义 → gRPC 状态码（A3「可程序化区分」验收）。

直接调纯函数 map_store_error（无需真实 server/库），用例与 node/test/error-matrix.test.js 同构。
规范依据：spec/03-errors-context.md（Permission / NoContext 同属权限类 ⇒ PERMISSION_DENIED）。
"""

import pytest

grpc = pytest.importorskip("grpc")

from store_grpc.errors import map_store_error  # noqa: E402

STATUS = grpc.StatusCode


class PermissionError(Exception):
    """store 权限错误类（Python 类名即 name）。"""


def _err(message: str, code: str) -> Exception:
    e = Exception(message)
    e.code = code
    return e


def test_matrix_permission_to_permission_denied():
    r = map_store_error(PermissionError("RBAC 拒绝"), PermissionError)
    assert r.status == STATUS.PERMISSION_DENIED
    assert r.code == "PermissionError"


def test_matrix_no_context_code_to_permission_denied():
    r = map_store_error(_err("上下文缺失", "no_context"), PermissionError)
    assert r.status == STATUS.PERMISSION_DENIED
    assert r.code == "no_context"


def test_matrix_no_context_prefix_to_permission_denied():
    r = map_store_error(Exception("ERR_NO_CONTEXT:上下文缺失"), PermissionError)
    assert r.status == STATUS.PERMISSION_DENIED
    assert r.code == "no_context"


def test_matrix_other_to_internal():
    r = map_store_error(Exception("数据库连接失败"), PermissionError)
    assert r.status == STATUS.INTERNAL
    assert r.details == "数据库连接失败"