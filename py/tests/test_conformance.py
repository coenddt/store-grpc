"""conformance runner — 读取共享用例 JSON（../../conformance/cases/*.json）逐项执行，
与 node 端 runner 断言同一份文件（spec 唯一事实源）。
"""

import json
import os
import re

import pytest

grpc = pytest.importorskip("grpc")
pytest.importorskip("grpc_tools")

from store_grpc import create_server  # noqa: E402

CASES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "conformance", "cases")

STATUS_BY_NAME = {
    "INVALID_ARGUMENT": grpc.StatusCode.INVALID_ARGUMENT,
    "UNAUTHENTICATED": grpc.StatusCode.UNAUTHENTICATED,
    "PERMISSION_DENIED": grpc.StatusCode.PERMISSION_DENIED,
    "NOT_FOUND": grpc.StatusCode.NOT_FOUND,
    "INTERNAL": grpc.StatusCode.INTERNAL,
}

REQ_FIELD_BY_RPC = {
    "List": "ListRequest",
    "Get": "GetRequest",
    "Create": "CreateRequest",
    "Update": "UpdateRequest",
    "Delete": "DeleteRequest",
}


class PermissionErrorMock(Exception):
    pass


def make_mock_store(defn):
    rows = []

    def guard_gql(gql):
        # 模拟 core 的 ERR_GQL_PARSE: 前缀契约：括号不配平即解析失败
        if gql.count("(") != gql.count(")"):
            raise ValueError("ERR_GQL_PARSE:括号不配平")

    class Store:
        PermissionError = PermissionErrorMock
        list = staticmethod(lambda: [defn["name"]])
        get = staticmethod(lambda name: defn)

        @staticmethod
        def query(gql, params=None):
            guard_gql(gql)
            cond = (params or {}).get("c0")
            return [r for r in rows if not cond or all(r.get(k) == v for k, v in cond.items())]

        @staticmethod
        def query_one(gql, params=None):
            guard_gql(gql)
            cond = (params or {}).get("c0")
            return next((r for r in rows if all(r.get(k) == v for k, v in cond.items())), None)

        @staticmethod
        def insert(name, data):
            doc = {"_id": f"u{len(rows) + 1}", **data}
            rows.append(doc)
            return doc

        @staticmethod
        def update(name, cond, data):
            row = next(r for r in rows if all(r.get(k) == v for k, v in cond.items()))
            row.update(data)
            return row

        @staticmethod
        def remove(name, cond):
            for i, r in enumerate(rows):
                if all(r.get(k) == v for k, v in cond.items()):
                    rows.pop(i)
                    return 1
            return 0

        @staticmethod
        def set_context(ctx):
            pass

    return Store()


def encode_request(pb, svc, step_request):
    """conformance 通用形状 → py 侧 snake_case 字段；字符串值原样传递（预编码/非法样本），对象才 dumps。"""
    verb = re.match(r"^(List|Get|Create|Update|Delete)", step_request.get("rpc", "List")).group(1)
    req_cls = getattr(pb, REQ_FIELD_BY_RPC[verb])
    kwargs = {}
    if step_request.get("q") is not None:
        kwargs["q"] = step_request["q"]
    if step_request.get("id") is not None:
        kwargs["id"] = step_request["id"]
    if step_request.get("params") is not None:
        p = step_request["params"]
        kwargs["params_json"] = p if isinstance(p, str) else json.dumps(p)
    if step_request.get("body") is not None:
        kwargs["body_json"] = json.dumps(step_request["body"])
    if step_request.get("set") is not None:
        kwargs["set_json"] = json.dumps(step_request["set"])
    return req_cls(**kwargs)


@pytest.mark.parametrize("file", [f for f in os.listdir(CASES_DIR) if f.endswith(".json")])
def test_conformance(file):
    with open(os.path.join(CASES_DIR, file), encoding="utf-8") as f:
        spec = json.load(f)
    svc = spec["service"]
    defn = {"name": svc, "fields": {"_id": {"type": "string"}, "name": {"type": "string"}, "age": {"type": "int"}}}
    store = make_mock_store(defn)
    server = create_server(store, port=0)
    try:
        pb = server.pb
        channel = grpc.insecure_channel(f"127.0.0.1:{server.port}")
        try:
            created_id = None
            for i, step in enumerate(spec["steps"]):
                request = encode_request(pb, svc, {"rpc": step["rpc"], **(step.get("request") or {})})
                if step.get("useCreatedId") and getattr(request, "id", "") == "":
                    request.id = created_id
                unary = channel.unary_unary(
                    f"/store.v0.{svc}/{step['rpc']}",
                    request_serializer=type(request).SerializeToString,
                    response_deserializer=pb.StoreReply.FromString,
                )
                try:
                    reply = unary(request, timeout=10)
                    error = None
                except grpc.RpcError as e:
                    reply = None
                    error = e

                expect = step["expect"]
                if expect.get("ok"):
                    assert error is None, f"step#{i} {step['rpc']} 应成功: {error and error.details}"
                    data = json.loads(reply.data_json)
                    if "dataKeys" in expect:
                        assert sorted(k for k in data if k in expect["dataKeys"]) == sorted(expect["dataKeys"])
                    if "listLength" in expect:
                        assert len(data) == expect["listLength"]
                    if i == 0:
                        created_id = data["_id"]
                else:
                    assert error is not None, f"step#{i} {step['rpc']} 应失败"
                    assert error.code() == STATUS_BY_NAME[expect["statusCode"]], f"step#{i} status"
                    assert dict(error.trailing_metadata())["store-error-code"] == expect["errorCode"], f"step#{i} code"
        finally:
            channel.close()
    finally:
        server.stop(None)
