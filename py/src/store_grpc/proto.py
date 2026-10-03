"""spec/01 — proto 文本生成（与 node 端 buildProto 逐字一致，双端 byte-identical）。"""

from __future__ import annotations

from typing import Any

ARCHIVE_SUFFIX = "Deleted"
PACKAGE = "store.v0"

_COMMON_MESSAGES = """message ListRequest {
  string q = 1;           // 模型名之后的 GQL 余部（参数列表与投影，REST ?q= 同源）；空串 ⇒ 服务端补 schema 投影
  string params_json = 2; // GQL params（如 {"c0":{"age":{"$gte":18}}}）的 JSON 编码
}

message GetRequest     { string id = 1; }

message CreateRequest  { string body_json = 1; }

message UpdateRequest  { string id = 1; string set_json = 2; }

message DeleteRequest  { string id = 1; }

message StoreReply     { string data_json = 1; }
"""

_RPC_COMMENTS = {
    "list": "列表查询：q 为模型名之后的 GQL 余部（参数列表与投影，与 REST ?q= 同源）；空串 ⇒ 服务端按 schema 投影",
    "get": "单条查询：按 id_field 精确匹配；无记录 ⇒ NOT_FOUND",
    "create": "插入：body_json 必须为 JSON 对象编码",
    "update": "按 id_field 定位后部分更新：set_json 必须为 JSON 对象编码；返回 store.update 原始结果",
    "remove": "按 id_field 定位删除：返回 store.remove 原始结果",
}


def filter_archived(names: list[str]) -> list[str]:
    """归档表过滤（spec/01）：`XxxDeleted` 且 `Xxx` 也在列表中 ⇒ 视为归档表（与 store-api / store-graphql 逐字一致）。"""
    s = set(names)
    return [n for n in names if not (n.endswith(ARCHIVE_SUFFIX) and n[: -len(ARCHIVE_SUFFIX)] in s)]


def service_name(name: str) -> str:
    """spec/01：service 名 = 模型名首字母大写、其余原样。"""
    return name[0].upper() + name[1:] if name else name


def _x_grpc(defn: Any) -> dict:
    return (defn or {}).get("x-grpc") or {}


def collect_services(store: Any, resources: list[str] | None = None) -> list[dict]:
    """收集待生成 service（归档过滤 + x-grpc 注记 + Pascal 冲突检测）。"""
    names = resources if resources is not None else filter_archived(store.list())
    seen: dict[str, str] = {}
    out: list[dict] = []
    get = getattr(store, "get", None)
    for name in names:
        defn = None
        if callable(get):
            try:
                defn = get(name)
            except KeyError:
                defn = None
        xg = _x_grpc(defn if isinstance(defn, dict) else None)
        if xg.get("hidden"):
            continue  # spec/01 注记：模型级 hidden
        svc = service_name(name)
        if svc in seen:
            raise ValueError(
                f"ERR_NAME_CONFLICT:模型 \"{name}\" 与 \"{seen[svc]}\" 的 service 名 \"{svc}\" 冲突（spec/01）"
            )
        seen[svc] = name
        description = (defn or {}).get("description") if isinstance(defn, dict) else None
        out.append({"name": name, "svc": svc, "readonly": bool(xg.get("readonly")), "description": description or ""})
    return out


def _render_service(s: dict) -> str:
    lines: list[str] = []
    if s["description"]:
        lines.append(f"// {s['description']}")
    lines.append(f"service {s['svc']} {{")
    lines.append(f"  // {_RPC_COMMENTS['list']}")
    lines.append(f"  rpc List{s['svc']} (ListRequest) returns (StoreReply);")
    lines.append(f"  // {_RPC_COMMENTS['get']}")
    lines.append(f"  rpc Get{s['svc']} (GetRequest) returns (StoreReply);")
    if not s["readonly"]:
        lines.append(f"  // {_RPC_COMMENTS['create']}")
        lines.append(f"  rpc Create{s['svc']} (CreateRequest) returns (StoreReply);")
        lines.append(f"  // {_RPC_COMMENTS['update']}")
        lines.append(f"  rpc Update{s['svc']} (UpdateRequest) returns (StoreReply);")
        lines.append(f"  // {_RPC_COMMENTS['remove']}")
        lines.append(f"  rpc Delete{s['svc']} (DeleteRequest) returns (StoreReply);")
    lines.append("}")
    return "\n".join(lines)


def build_proto(store: Any, *, resources: list[str] | None = None) -> str:
    """spec/01：schema → .proto 文本（纯构建，零 gRPC 依赖）。"""
    services = collect_services(store, resources)
    return (
        "// 由 store-grpc 自动生成 —— 事实源：store 的 JSON schema（与 store-api / store-graphql 同源）\n"
        'syntax = "proto3";\n\n'
        f"package {PACKAGE};\n\n"
        f"{_COMMON_MESSAGES}\n"
        + "\n\n".join(_render_service(s) for s in services)
        + "\n"
    )


def export_proto(store: Any, *, resources: list[str] | None = None) -> str:
    """spec/01：export_proto ≡ build_proto（命名对齐 store-graphql 的 export_sdl）。"""
    return build_proto(store, resources=resources)
