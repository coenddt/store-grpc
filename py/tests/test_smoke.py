"""冒烟测试 — mock store + 真实 grpcio server（本地随机端口，不连真实库）。

用例语义与 node 端 smoke / ../../conformance/cases/users-crud.json 对齐（spec 唯一事实源）。
"""

import json
import re

import pytest

grpc = pytest.importorskip("grpc")
pytest.importorskip("grpc_tools")

from store_grpc import build_proto, create_server, export_proto, filter_archived, service_name  # noqa: E402

DEFN = {
    "name": "User",
    "description": "用户表：平台账号主档",
    "fields": {
        "_id": {"type": "string", "description": "主键，u 前缀"},
        "name": {"type": "string"},
        "age": {"type": "int"},
        "profile": {"type": "object", "description": "个人资料", "fields": {"bio": {"type": "string"}}},
    },
}

STATUS = grpc.StatusCode
REQ_CLASS_BY_RPC = {
    "List": "ListRequest",
    "Get": "GetRequest",
    "Create": "CreateRequest",
    "Update": "UpdateRequest",
    "Delete": "DeleteRequest",
}


class PermissionErrorMock(Exception):
    """实例 name 显式化（真实 store 权限类有稳定码/name；Python 类名即 __name__）。"""


def make_mock_store():
    rows = []
    state = {"last_ctx": "initial-not-cleaned"}

    def guard_gql(gql):
        # 模拟 core 的 ERR_GQL_PARSE: 前缀契约：括号不配平即解析失败（conformance GQL_PARSE 用例依赖）
        if gql.count("(") != gql.count(")"):
            raise ValueError("ERR_GQL_PARSE:括号不配平")

    class Store:
        PermissionError = PermissionErrorMock
        list = staticmethod(lambda: ["User"])
        get = staticmethod(lambda name: DEFN)

        @staticmethod
        async def query(gql, params=None):
            guard_gql(gql)
            cond = (params or {}).get("c0")
            return [r for r in rows if not cond or all(r.get(k) == v for k, v in cond.items())]

        @staticmethod
        async def query_one(gql, params=None):
            guard_gql(gql)
            cond = (params or {}).get("c0")
            return next((r for r in rows if all(r.get(k) == v for k, v in cond.items())), None)

        @staticmethod
        async def insert(name, data):
            doc = {"_id": f"u{len(rows) + 1}", **data}
            rows.append(doc)
            return doc

        @staticmethod
        async def update(name, cond, data):
            row = next(r for r in rows if all(r.get(k) == v for k, v in cond.items()))
            row.update(data)
            return row

        @staticmethod
        async def remove(name, cond):
            for i, r in enumerate(rows):
                if all(r.get(k) == v for k, v in cond.items()):
                    rows.pop(i)
                    return 1
            return 0

        @staticmethod
        async def set_context(ctx):
            state["last_ctx"] = ctx

    return Store(), rows, state


def err_details(e):
    """grpcio 错误 details：活跃错误为属性、_InactiveRpcError 为方法，统一取值。"""
    d = e.details
    return d() if callable(d) else d


def make_client(h, svc="User", readonly=False):
    """用 server 暴露的 proto 文本编译产物构造 client（与生产客户端同路径）。"""
    pb = h.pb
    verbs = ["List", "Get"] if readonly else ["List", "Get", "Create", "Update", "Delete"]

    def call(rpc, request):
        verb = re.match(r"^(List|Get|Create|Update|Delete)", rpc).group(1)
        req_cls = getattr(pb, REQ_CLASS_BY_RPC[verb])
        reply_cls = pb.StoreReply
        channel = grpc.insecure_channel(f"127.0.0.1:{h.port}")
        try:
            unary = channel.unary_unary(
                f"/store.v0.{svc}/{rpc}",
                request_serializer=req_cls.SerializeToString,
                response_deserializer=reply_cls.FromString,
            )
            try:
                return {"reply": unary(request, timeout=10)}
            except grpc.RpcError as e:
                return {"error": e}
        finally:
            channel.close()

    return call


@pytest.fixture()
def h():
    store, rows, state = make_mock_store()
    server = create_server(store, port=0)
    yield server, store, rows, state
    server.stop(None)


def test_filter_archived():
    assert filter_archived(["User", "UserDeleted", "Log"]) == ["User", "Log"]


def test_service_name():
    assert service_name("user") == "User"
    assert service_name("User") == "User"


def test_build_proto():
    store, _, _ = make_mock_store()
    proto = build_proto(store)
    assert 'syntax = "proto3";' in proto
    assert "package store.v0;" in proto
    assert "service User {" in proto
    assert "rpc ListUser (ListRequest) returns (StoreReply);" in proto
    assert "rpc GetUser (GetRequest) returns (StoreReply);" in proto
    assert "rpc CreateUser (CreateRequest) returns (StoreReply);" in proto
    assert "rpc UpdateUser (UpdateRequest) returns (StoreReply);" in proto
    assert "rpc DeleteUser (DeleteRequest) returns (StoreReply);" in proto
    assert "// 用户表：平台账号主档" in proto  # description 透传（spec/01）
    assert export_proto(store) == proto  # export_proto ≡ build_proto（spec/01）


def test_build_proto_notes_and_conflict():
    store, _, _ = make_mock_store()
    store.list = lambda: ["User", "SecretLog", "AuditEvent"]
    store.get = lambda n: (
        DEFN
        if n == "User"
        else {**DEFN, "name": "SecretLog", "x-grpc": {"hidden": True}}
        if n == "SecretLog"
        else {**DEFN, "name": "AuditEvent", "x-grpc": {"readonly": True}}
    )
    proto = build_proto(store)
    assert "SecretLog" not in proto
    assert "service AuditEvent {" in proto
    assert "rpc ListAuditEvent" in proto
    assert "rpc CreateAuditEvent" not in proto  # readonly → 只出查询两段

    store.list = lambda: ["user", "User"]
    with pytest.raises(ValueError, match="ERR_NAME_CONFLICT"):
        build_proto(store)


def test_five_rpc_full_chain(h):
    server, store, rows, _ = h
    call = make_client(server)
    pb = server.pb

    # CreateUser
    r = call("CreateUser", pb.CreateRequest(body_json=json.dumps({"name": "alice", "age": 30})))
    assert r["reply"].data_json == '{"_id": "u1", "name": "alice", "age": 30}'

    # ListUser（q 透传 + params_json）
    r = call(
        "ListUser",
        pb.ListRequest(q="($condition: @c0) { name, age }", params_json=json.dumps({"c0": {"age": 30}})),
    )
    assert json.loads(r["reply"].data_json)[0]["name"] == "alice"

    # ListUser（q 缺省 → schema 投影拼接；投影串由适配层拼进 GQL）
    r = call("ListUser", pb.ListRequest())
    assert r["reply"].data_json  # 成功即可，投影语义由 smoke 的 gqlLog 用例覆盖

    # GetUser
    r = call("GetUser", pb.GetRequest(id="u1"))
    assert json.loads(r["reply"].data_json) == {"_id": "u1", "name": "alice", "age": 30}

    # UpdateUser（返回 store.update 原始结果，不做二次回读——spec/02 对齐 REST）
    r = call("UpdateUser", pb.UpdateRequest(id="u1", set_json=json.dumps({"age": 31})))
    assert json.loads(r["reply"].data_json) == {"_id": "u1", "name": "alice", "age": 31}

    # DeleteUser
    r = call("DeleteUser", pb.DeleteRequest(id="u1"))
    assert json.loads(r["reply"].data_json) == 1

    # 删除后 GetUser ⇒ NOT_FOUND + trailer store-error-code（spec/03）
    r = call("GetUser", pb.GetRequest(id="u1"))
    err = r["error"]
    assert err.code() == STATUS.NOT_FOUND
    assert err_details(err) == "记录不存在: _id=u1"
    assert dict(err.trailing_metadata())["store-error-code"] == "NOT_FOUND"


def test_error_mapping(h):
    server, store, _, _ = h
    call = make_client(server)
    pb = server.pb

    # body_json 数组 / 非法 JSON ⇒ INVALID_ARGUMENT + INVALID_BODY
    assert call("CreateUser", pb.CreateRequest(body_json="[1,2]"))["error"].code() == STATUS.INVALID_ARGUMENT
    r = call("CreateUser", pb.CreateRequest(body_json="{oops"))
    assert dict(r["error"].trailing_metadata())["store-error-code"] == "INVALID_BODY"

    # params_json 非法 JSON ⇒ INVALID_PARAM
    r = call("ListUser", pb.ListRequest(params_json="not-json"))
    assert r["error"].code() == STATUS.INVALID_ARGUMENT
    assert dict(r["error"].trailing_metadata())["store-error-code"] == "INVALID_PARAM"

    # core GQL 解析失败（ERR_GQL_PARSE: 前缀）⇒ GQL_PARSE，details 剥前缀
    r = call("ListUser", pb.ListRequest(q="($bogus"))
    assert dict(r["error"].trailing_metadata())["store-error-code"] == "GQL_PARSE"
    assert err_details(r["error"]) == "括号不配平"

    # ERR_PERMISSION: 前缀 ⇒ PERMISSION_DENIED
    orig_query = store.query

    def deny(gql, params=None):
        raise ValueError("ERR_PERMISSION:无访问权限")

    store.query = deny
    r = call("ListUser", pb.ListRequest())
    assert r["error"].code() == STATUS.PERMISSION_DENIED
    assert dict(r["error"].trailing_metadata())["store-error-code"] == "ERR_PERMISSION"

    # PermissionError 类（store.PermissionError 来源）⇒ PERMISSION_DENIED，码取 store_code

    def deny_class(gql, params=None):
        raise PermissionErrorMock("RBAC 拒绝")

    store.query = deny_class
    r = call("ListUser", pb.ListRequest())
    assert r["error"].code() == STATUS.PERMISSION_DENIED
    assert dict(r["error"].trailing_metadata())["store-error-code"] == "PermissionErrorMock"

    # 其余 store 错误 ⇒ INTERNAL，message 原样透传（禁掩盖）

    def boom(gql, params=None):
        raise RuntimeError("数据库连接失败")

    store.query = boom
    r = call("ListUser", pb.ListRequest())
    assert r["error"].code() == STATUS.INTERNAL
    assert err_details(r["error"]) == "数据库连接失败"
    assert dict(r["error"].trailing_metadata())["store-error-code"] == "RuntimeError"


def test_context_provider(h):
    server, store, _, state = h
    call = make_client(server)
    pb = server.pb

    def provider(metadata):
        user = metadata.get("x-user")
        if user == "bad":
            raise ValueError("ERR_PERMISSION:无访问权限")
        if user == "broken":
            raise ValueError("上下文钩子故障")
        if user == "anon":
            return None  # 显式空上下文
        return {"user": user}

    # 重新起一个带 provider 的 server
    server.stop(None)
    store2, rows2, state2 = make_mock_store()
    server2 = create_server(store2, port=0, context_provider=provider)
    try:
        call2 = make_client(server2)
        chan = grpc.insecure_channel(f"127.0.0.1:{server2.port}")
        unary = chan.unary_unary(
            "/store.v0.User/ListUser",
            request_serializer=pb.ListRequest.SerializeToString,
            response_deserializer=pb.StoreReply.FromString,
        )

        def raw(user):
            md = (("x-user", user),)
            try:
                unary(pb.ListRequest(q=" { _id }"), metadata=md, timeout=10)
                return None
            except grpc.RpcError as e:
                return e

        # 注入：set_context 收到 provider 返回的对象
        raw("alice")
        assert state2["last_ctx"] == {"user": "alice"}

        # ERR_PERMISSION: 前缀 ⇒ PERMISSION_DENIED
        err = raw("bad")
        assert err.code() == STATUS.PERMISSION_DENIED

        # 非权限类 ⇒ UNAUTHENTICATED，message 原样透传
        err = raw("broken")
        assert err.code() == STATUS.UNAUTHENTICATED
        assert err_details(err) == "上下文钩子故障"

        # 返回 None ⇒ 显式 set_context(None)（清除语义落地，防上一请求上下文残留）
        raw("anon")
        assert state2["last_ctx"] is None
        chan.close()
    finally:
        server2.stop(None)


def test_readonly_model():
    store, _, _ = make_mock_store()
    store.list = lambda: ["AuditEvent"]
    store.get = lambda n: {**DEFN, "name": n, "x-grpc": {"readonly": True}}
    server = create_server(store, port=0)
    try:
        call = make_client(server, svc="AuditEvent", readonly=True)
        pb = server.pb
        assert json.loads(call("ListAuditEvent", pb.ListRequest())["reply"].data_json) == []
        assert call("GetAuditEvent", pb.GetRequest(id="x"))["error"].code() == STATUS.NOT_FOUND
    finally:
        server.stop(None)


def test_proto_roundtrip():
    store, _, _ = make_mock_store()
    proto = export_proto(store)
    from grpc_tools import protoc
    import os
    import tempfile

    tmp = tempfile.mkdtemp()
    f = os.path.join(tmp, "store_grpc.proto")
    with open(f, "w", encoding="utf-8") as fh:
        fh.write(proto)
    assert protoc.main(["protoc", f"-I{tmp}", f"--python_out={tmp}", f]) == 0  # 可再编译 = 合法 IDL
