# 02 — 执行映射：rpc → store 调用

所有 rpc 的 handler 遵循同一骨架：解析请求 JSON → 调 store 原生 API → 成功结果 `JSON.stringify` 进 `StoreReply.data_json`；任何错误经 spec/03 判定链映射后终止调用。

## 映射表

以模型 `User`、service 名 `User`、id_field `_id`、schema 投影 `proj`（` { name, age }` 形状，见下）为例：

| rpc | 请求 | store 调用 | 成功语义 |
|---|---|---|---|
| `ListUser` | `q`、`params_json` | `store.query(q 或 name+proj, JSON.parse(params_json))` | 数组 JSON |
| `GetUser` | `id` | `store.queryOne('User($condition: @c0)'+proj, { c0: { _id: id } })` | 对象 JSON；`null` ⇒ NOT_FOUND |
| `CreateUser` | `body_json` | `store.insert('User', JSON.parse(body_json))` | 插入文档 JSON |
| `UpdateUser` | `id`、`set_json` | `store.update('User', { _id: id }, JSON.parse(set_json))` | store.update 返回值原样 JSON（对齐 REST PATCH，不做二次回读） |
| `DeleteUser` | `id` | `store.remove('User', { _id: id })` | store.remove 返回值原样 JSON |

## 请求 JSON 解析规则

| 字段 | 规则 | 违例 |
|---|---|---|
| `params_json` | 空串 ⇒ params 为 `undefined`（不传）；否则必须是合法 JSON | ⇒ INVALID_ARGUMENT + 码 `INVALID_PARAM`（spec/03） |
| `body_json` | 必须是 JSON **对象**编码（数组 / 标量 / 非法 JSON 拒绝） | ⇒ INVALID_ARGUMENT + 码 `INVALID_BODY` |
| `set_json` | 同 `body_json` | ⇒ INVALID_ARGUMENT + 码 `INVALID_BODY` |
| `q` | 空串 ⇒ 服务端拼 `name + proj`；非空 ⇒ 服务端拼 `name + q`（模型名之后的 GQL 余部，与 REST `?q=` 完全同源） | core 抛 `ERR_GQL_PARSE:` ⇒ spec/03 判定链 |
| `id` | 原样透传为定位值，服务端不做类型改写 | — |

## schema 投影（proj）

从 store 元数据生成显式投影串 ` { f1, f2 }`（fields + computes 均视为字段，与 store-graphql 对齐）：

- 取值顺序：`store.get(name)`（nodejs 形态）→ py-store 的 `schema` 模块（py-store 未在 Store 类暴露 get 时）。
- 两者皆不可得 / fields 为空 ⇒ 空串（无投影，data 仅 `_id`——上游 schema 定义不完整的**显式后果**，与 REST 同语义）。
- 仅 `ListUser` 的 q 缺失时使用 proj；q 存在时投影完全由 q 决定，适配层不追加。`GetUser` 恒用 proj。

## limit 守卫：本皮肤不设

GraphQL 皮肤的 limit 缺省 50 / 上限 1000 属其 spec；REST 与 gRPC 的 q 透传不做行数守卫（core 的行数封顶在 text2query 档生效）。本皮肤与 REST 对齐。

## 上下文注入（contextProvider）

- `opts.contextProvider`：每 rpc 调用前的钩子，收到本次调用的元数据（node：grpc-js 的 `call.metadata`；py：`ServicerContext.invocation_metadata()`）。
- 返回值经 `store.setContext(ctx)` 注入；返回 `null`/`undefined` 时同样**显式** `setContext(null)`（清除语义必须落地——有状态持有的运行时禁止残留上一请求上下文，防身份跨请求泄漏）。
- 钩子抛错 ⇒ spec/03 判定链（PermissionError ⇒ PERMISSION_DENIED；其余 ⇒ UNAUTHENTICATED，message 原样透传）。

## server API

- node：`createServer(store, opts) -> Promise<{ server, proto, port, shutdown() }>`；`opts.port` 给定时 `bindAsync` 完成后返回（`port: 0` ⇒ 随机端口），未给定时仅注册不监听。
- py：`create_server(store, opts) -> GrpcServer`（同步；`.port` / `.stop(grace)` 同构）。
- 承载依赖均为 optional：node 缺 `@grpc/grpc-js` / py 缺 `grpcio`+`grpcio-tools` 时 `createServer` / `create_server` 抛带安装指引的错误；`buildProto` / `exportProto` 永远可用。

## py 端承载差异：同步 grpcio + async store 双兼容

py-store 的 store 实例方法为 async（REST / GraphQL 皮肤均 `await` 它们），而 py 承载用同步 grpcio（工作线程池）。约定：适配层对 store 调用与 `context_provider` 的返回值做 awaitable 检测——awaitable 在工作线程内经私有事件循环（`asyncio.run`）执行，同步返回值直接使用。两种宿主形态行为一致，适配层不做语义改写。
