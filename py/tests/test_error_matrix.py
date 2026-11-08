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
    # 旧 trailer 值原地不动（store_code 链 = 类名），规范化码另置
    assert r.code == "PermissionError"
    assert r.normalized == "ERR_PERMISSION"


def test_matrix_no_context_code_to_permission_denied():
    r = map_store_error(_err("上下文缺失", "no_context"), PermissionError)
    assert r.status == STATUS.PERMISSION_DENIED
    assert r.code == "no_context"


def test_matrix_no_context_prefix_to_permission_denied():
    r = map_store_error(Exception("ERR_NO_CONTEXT:上下文缺失"), PermissionError)
    assert r.status == STATUS.PERMISSION_DENIED
    assert r.code == "no_context"


def test_matrix_not_found_code_to_not_found():
    r = map_store_error(_err("行不存在", "ERR_NOT_FOUND"), PermissionError)
    assert r.status == STATUS.NOT_FOUND
    assert r.code == "NOT_FOUND"
    assert r.details == "行不存在"


def test_matrix_not_found_prefix_to_not_found():
    r = map_store_error(Exception("ERR_NOT_FOUND:行不存在"), PermissionError)
    assert r.status == STATUS.NOT_FOUND
    assert r.code == "NOT_FOUND"
    assert r.details == "ERR_NOT_FOUND:行不存在"


def test_matrix_resource_not_found_prefix_to_not_found():
    """第二轮：资源无任何可读副本 ⇒ NOT_FOUND，details 剥前缀（对齐 REST spec/03）。"""
    r = map_store_error(Exception("ERR_RESOURCE_NOT_FOUND:资源无任何可读副本"), PermissionError)
    assert r.status == STATUS.NOT_FOUND
    assert r.code == "NOT_FOUND"
    assert r.details == "资源无任何可读副本"


def test_matrix_other_to_internal():
    r = map_store_error(Exception("数据库连接失败"), PermissionError)
    assert r.status == STATUS.INTERNAL
    assert r.details == "数据库连接失败"