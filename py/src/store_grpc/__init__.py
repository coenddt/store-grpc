"""store_grpc_py — 为 py-store 已注册 schema 自动生成 gRPC service。

语义依据：../../spec/*.md（双端 parity，改动先改 spec）。
设计哲学：gRPC 只是 GQL 的又一层 RPC 皮肤 —— 适配层零语义发明。
proto-first 双出口：
  build_proto(store, **opts)  → .proto 文本（IDL 事实源，纯构建，零 gRPC 依赖）
  export_proto(store, **opts) → 同上（命名对齐 store-graphql 的 export_sdl，喂外部 protoc / codegen）
  create_server(store, **opts) → grpcio server（同一份 proto 文本运行时编译注册，零手工 protoc）

store 端口契约见 spec/00：list/get/query/query_one/insert/update/remove/set_context。
"""

from .proto import build_proto, export_proto, filter_archived, service_name

__all__ = [
    "build_proto",
    "export_proto",
    "filter_archived",
    "service_name",
    "create_server",
]


def create_server(*args, **kwargs):
    """延迟导入承载层（grpcio + grpcio-tools 为可选依赖）。"""
    from .server import create_server as impl

    return impl(*args, **kwargs)
