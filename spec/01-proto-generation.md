# 01 — proto 生成：schema → .proto 映射

## 生成源唯一

store 的纯 JSON schema（`store.list()` / `store.get(name)`），与 REST 皮肤 store-api、GraphQL 皮肤 store-graphql 同源。

## 生成面

每个模型一个 service，固定五段 rpc：

| rpc | 请求 message | store 调用 | 说明 |
|---|---|---|---|
| `List{Name}` | `ListRequest` | `store.query` | 列表查询（q 透传） |
| `Get{Name}` | `GetRequest` | `store.queryOne` | 单条查询；空结果 ⇒ NOT_FOUND（spec/02） |
| `Create{Name}` | `CreateRequest` | `store.insert` | 插入 |
| `Update{Name}` | `UpdateRequest` | `store.update` | 按 id_field 定位部分更新 |
| `Delete{Name}` | `DeleteRequest` | `store.remove` | 按 id_field 定位删除 |

## 共用 message 形状（v0：JSON string 承载动态数据）

```proto
syntax = "proto3";

package store.v0;

service User {
  // 列表查询：q 为模型名之后的 GQL 余部（参数列表与投影，与 REST ?q= 同源）；空串 ⇒ 服务端按 schema 投影
  rpc ListUser (ListRequest) returns (StoreReply);
  // 单条查询：按 id_field 精确匹配；无记录 ⇒ NOT_FOUND
  rpc GetUser (GetRequest) returns (StoreReply);
  // 插入：body_json 必须为 JSON 对象编码
  rpc CreateUser (CreateRequest) returns (StoreReply);
  // 按 id_field 定位后部分更新：set_json 必须为 JSON 对象编码；返回 store.update 原始结果
  rpc UpdateUser (UpdateRequest) returns (StoreReply);
  // 按 id_field 定位删除：返回 store.remove 原始结果
  rpc DeleteUser (DeleteRequest) returns (StoreReply);
}

message ListRequest {
  string q = 1;           // 模型名之后的 GQL 余部（参数列表与投影，REST ?q= 同源）；空串 ⇒ 服务端补 schema 投影
  string params_json = 2; // GQL params（如 {"c0":{"age":{"$gte":18}}}）的 JSON 编码
}

message GetRequest     { string id = 1; }
message CreateRequest  { string body_json = 1; }
message UpdateRequest  { string id = 1; string set_json = 2; }
message DeleteRequest  { string id = 1; }
message StoreReply     { string data_json = 1; }
```

决策记录：v0 用 JSON string 而非 `google.protobuf.Struct` 或逐字段强类型 message。理由：GQL 查询串与 params 本就是动态 JSON，string 承载零歧义、四端序列化成本最低（与「双端 parity 成本最低」哲学一致）；逐字段强类型化是 v1 演进方向（schema 反射 → message 字段），须先改本 spec。

**语言惯例差异（显式声明，非 parity 缺陷）**：`.proto` 文件字段名恒为 snake_case（`params_json` / `data_json`）；各语言运行时按其 protobuf 惯例呈现——Python 的 `_pb2` 字段即 `body_json`，Node（protobufjs）的 JS 对象字段为 `bodyJson`，Go 生成代码为 `BodyJson`。适配层不跨语义改写，客户端按目标语言的生成代码惯例访问。

## 命名映射

- **service 名** = 模型名首字母大写、其余原样（`user` → `User`，`user_profile` → `User_profile`）。
- **rpc 名** = 动词前缀（`List` / `Get` / `Create` / `Update` / `Delete`）+ service 名。
- **package** 固定 `store.v0`。
- **PascalCase 冲突检测**：两个模型名首字母大写后相同（如 `user` 与 `User`）⇒ 构建期抛错（`ERR_NAME_CONFLICT:` 前缀），禁静默合并。

## 归档表过滤

与 store-api / store-graphql 逐字一致：`XxxDeleted` 且 `Xxx` 也在模型列表中 ⇒ 视为归档表，不生成 service。

## 注记（对齐 x-graphql，v0 只做注记级自定义）

| 注记 | 语义 |
|---|---|
| `"x-grpc": {"hidden": true}` | 整个 service 不生成 |
| `"x-grpc": {"readonly": true}` | 只生成 List / Get 两段 rpc |

## description 透传

模型级 `description` 写入 service 的 `//` 注释；rpc 注释为固定文案（上表「说明」列）。适配层透传不改写（与 store-graphql 的 description 管道同构）。

## resources 与 id_field

- `resources`：显式资源名列表；缺省取 `store.list()` 并过滤归档表。
- `id_field`：单条 rpc 主键字段名，缺省 `_id`（双端一致）。

## 输出 API

- `buildProto(store, opts) -> string`：纯构建，零 gRPC 依赖。
- `exportProto(store, opts)`：同 `buildProto`，命名对齐 store-graphql 的 `exportSDL`（给外部 protoc / grpcurl / 客户端 codegen 用的落盘产物）。
