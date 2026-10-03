"""spec/02 — gRPC 承载（grpcio + grpcio-tools，可选依赖；同一份 proto 文本运行时编译，零手工 protoc）。"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
import re

import tempfile
from typing import Any, Callable

from .errors import (
    ContextError,
    MappedError,
    invalid_body,
    invalid_param,
    map_store_error,
    not_found,
    parse_json_field,
    require_json_object,
)
from .proto import PACKAGE, build_proto, collect_services

ContextProvider = Callable[[Any], Any]

_REQ_CLASS_BY_VERB = {
    "List": "ListRequest",
    "Get": "GetRequest",
    "Create": "CreateRequest",
    "Update": "UpdateRequest",
    "Delete": "DeleteRequest",
}


_COMPILED_TYPE_NAMES = (
    "ListRequest",
    "GetRequest",
    "CreateRequest",
    "UpdateRequest",
    "DeleteRequest",
    "StoreReply",
)


class _Compiled:
    """编译产物：按 message 名取消息类（pb.ListRequest / pb.StoreReply ...）。"""

    def __init__(self, classes: dict):
        for name, cls in classes.items():
            setattr(self, name, cls)


def _compile_proto(proto_text: str):
    """grpc_tools.protoc 编译 proto 文本 → 独立 descriptor pool 的消息类集合。

    用 --descriptor_set_out + 独立 DescriptorPool（非全局 Default pool）：不同 schema 组合
    反复编译时同名 message（store.v0.ListRequest 等）不冲突；进程内按内容 hash 缓存。
    """
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
    from grpc_tools import protoc

    cache = _compile_proto._cache
    key = hashlib.sha1(proto_text.encode("utf-8")).hexdigest()
    if key in cache:
        return cache[key]

    tmp = tempfile.mkdtemp(prefix="store-grpc-")
    proto_file = os.path.join(tmp, "store_grpc.proto")
    with open(proto_file, "w", encoding="utf-8") as f:
        f.write(proto_text)
    desc_file = os.path.join(tmp, "store_grpc.desc")
    rc = protoc.main(
        ["protoc", f"-I{tmp}", f"--descriptor_set_out={desc_file}", proto_file]
    )
    if rc != 0:
        raise RuntimeError(f"grpc_tools.protoc 编译失败 rc={rc}")

    fds = descriptor_pb2.FileDescriptorSet()
    with open(desc_file, "rb") as f:
        fds.ParseFromString(f.read())
    pool = descriptor_pool.DescriptorPool()
    for fd in fds.file:
        pool.Add(fd)

    classes = {
        name: message_factory.GetMessageClass(pool.FindMessageTypeByName(f"{PACKAGE}.{name}"))
        for name in _COMPILED_TYPE_NAMES
    }
    module = _Compiled(classes)
    cache[key] = module
    return module


_compile_proto._cache = {}


def _run_coro(coro):
    """同步承载执行协程：工作线程内建私有事件循环（py-store 实例方法为 async，spec/02 py 端差异）。"""
    import asyncio

    return asyncio.run(coro)


def _run_maybe_coro(fn, *args):
    result = fn(*args)
    if inspect.isawaitable(result):
        result = _run_coro(result)
    return result


async def _maybe_await(value):
    """store 方法同步 / async 双兼容（py-store 实例方法为 async，mock 可为同步）。"""
    if inspect.isawaitable(value):
        return await value
    return value


def _schema_projection(store: Any, name: str) -> str:
    """spec/02：fields + computes 均视为字段（与 store-graphql 对齐）；取不到 / 空 ⇒ 空串。"""
    fields: dict | None = None
    get = getattr(store, "get", None)
    if callable(get):
        try:
            meta = get(name)
        except KeyError:
            meta = None
        if isinstance(meta, dict):
            fields = {**(meta.get("fields") or {}), **(meta.get("computes") or {})}
    keys = list((fields or {}).keys())
    return f" {{ {', '.join(keys)} }}" if keys else ""


def _make_handlers(store: Any, s: dict, *, id_field: str, context_provider: ContextProvider | None, permission_error, pb):
    name, svc, readonly = s["name"], s["svc"], s["readonly"]
    proj = _schema_projection(store, name)
    store_set_context = store.set_context

    def wrap(fn: Callable[[Any], Any]):
        def behavior(request, context):
            try:
                if context_provider is not None:
                    try:
                        ctx = context_provider(dict(context.invocation_metadata() or ()))
                        if inspect.isawaitable(ctx):
                            # 同步 grpcio 承载：provider 的 awaitable 结果经私有事件循环执行
                            ctx = _run_coro(ctx)
                    except MappedError:
                        raise
                    except Exception as e:  # noqa: BLE001 — spec/02：权限类/前缀 ⇒ PERMISSION_DENIED；其余 ⇒ UNAUTHENTICATED
                        if permission_error is not None and isinstance(e, permission_error):
                            raise
                        if str(e).startswith("ERR_PERMISSION:"):
                            raise
                        raise ContextError(str(e) or "") from e
                    # spec/02：None 同样显式注入（set_context(None) 清除语义必须落地，防跨请求残留）
                    _run_maybe_coro(store_set_context, ctx)
                data = _run_maybe_coro(fn, request)
                return pb.StoreReply(data_json=json.dumps(data, ensure_ascii=False))
            except Exception as e:  # noqa: BLE001 — 单一错误出口：一切异常过 spec/03 判定链（禁静默）
                mapped = e if isinstance(e, MappedError) else map_store_error(e, permission_error)
                context.set_trailing_metadata((("store-error-code", mapped.code),))
                context.abort(mapped.status, mapped.details)

        return behavior

    async def _list(req):
        params = parse_json_field(req.params_json, invalid_param, "params_json")
        # spec/02：q 缺失 → schema 投影；q 存在 → 投影完全由 q 决定，适配层不追加
        gql = name + (req.q or proj)
        return await _maybe_await(store.query(gql, params))

    async def _get(req):
        data = await _maybe_await(store.query_one(f"{name}($condition: @c0){proj}", {"c0": {id_field: req.id}}))
        if data is None:
            raise not_found(f"记录不存在: {id_field}={req.id}")
        return data

    handlers = {
        f"List{svc}": wrap(_list),
        f"Get{svc}": wrap(_get),
    }
    if not readonly:

        async def _create(req):
            body = require_json_object(req.body_json, "body_json")
            if body is None:
                raise invalid_body("body_json 必须是 JSON 对象编码")
            return await _maybe_await(store.insert(name, body))

        async def _update(req):
            set_ = require_json_object(req.set_json, "set_json")
            if set_ is None:
                raise invalid_body("set_json 必须是 JSON 对象编码")
            return await _maybe_await(store.update(name, {id_field: req.id}, set_))

        async def _delete(req):
            return await _maybe_await(store.remove(name, {id_field: req.id}))

        handlers.update(
            {
                f"Create{svc}": wrap(_create),
                f"Update{svc}": wrap(_update),
                f"Delete{svc}": wrap(_delete),
            }
        )
    return handlers


class _Router:
    """按 method 路由（'/store.v0.{svc}/{rpc}'）→ RpcMethodHandler；未注册路径返回 None（grpcio ⇒ UNIMPLEMENTED）。"""

    def __init__(self, pb: Any, routes: dict[str, Callable], handler_factory: Callable):
        self._pb = pb
        self._routes = routes
        self._handler = handler_factory

    def service(self, handler_call_details):
        behavior = self._routes.get(handler_call_details.method or "")
        if behavior is None:
            return None
        verb = re.match(r"^(List|Get|Create|Update|Delete)", behavior.rpc).group(1)
        req_cls = getattr(self._pb, _REQ_CLASS_BY_VERB[verb])
        return self._handler(
            behavior,
            request_deserializer=req_cls.FromString,
            response_serializer=self._pb.StoreReply.SerializeToString,
        )


class GrpcServer:
    """承载句柄：.server / .pb / .proto / .port / .stop(grace)（与 node 端 { server, proto, port, shutdown } 同构）。"""

    def __init__(self, server: Any, pb: Any, proto: str, port: int | None):
        self.server = server
        self.pb = pb
        self.proto = proto
        self.port = port

    def stop(self, grace: float | None = None) -> None:
        self.server.stop(grace)


def create_server(
    store: Any,
    *,
    port: int | None = None,
    host: str = "127.0.0.1",
    resources: list[str] | None = None,
    id_field: str = "_id",
    context_provider: ContextProvider | None = None,
    permission_error: type[BaseException] | None = None,
) -> GrpcServer:
    """spec/02：注册 service 并按需监听。port 给定 ⇒ start 后返回（0 ⇒ 随机端口）；None ⇒ 仅注册不监听。

    承载依赖缺失 ⇒ 抛带安装指引的错误（禁静默降级）。
    """
    try:
        import grpc  # noqa: F401
        from grpc_tools import protoc  # noqa: F401
    except ImportError as e:  # noqa: F841 — 报错信息自身已含原因
        raise ImportError(
            "create_server 需要安装 grpcio 与 grpcio-tools：pip install 'store-grpc-py[grpc]'；"
            "或仅用 build_proto / export_proto 自行承载"
        ) from e

    import grpc

    proto_text = build_proto(store, resources=resources)
    pb = _compile_proto(proto_text)
    services = collect_services(store, resources)

    if permission_error is None:
        permission_error = getattr(store, "PermissionError", None)

    routes: dict[str, Any] = {}
    for s in services:
        handlers = _make_handlers(
            store, s, id_field=id_field, context_provider=context_provider, permission_error=permission_error, pb=pb
        )
        for rpc, behavior in handlers.items():
            behavior.rpc = rpc
            routes[f"/{PACKAGE}.{s['svc']}/{rpc}"] = behavior

    from concurrent import futures

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8), options=None)
    server.add_generic_rpc_handlers([_Router(pb, routes, grpc.unary_unary_rpc_method_handler)])

    if port is None:
        return GrpcServer(server, pb, proto_text, None)

    bound = server.add_insecure_port(f"{host}:{port}")
    if bound == 0:
        raise RuntimeError(f"grpc server 绑定端口失败: {host}:{port}")
    server.start()
    return GrpcServer(server, pb, proto_text, bound)
