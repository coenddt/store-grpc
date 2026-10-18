# store-grpc

为 common-store 数据层家族（nodejs-store / py-store / go-store）按 schema 自动生成**标准 gRPC** API——同一份 JSON schema 事实源之上的第三层皮肤（第一层 REST：[store-api](https://github.com/coenddt/store-api)，第二层 GraphQL：[store-graphql](https://github.com/coenddt/store-graphql)）。

这里的 gRPC **只是 GQL 的又一层 RPC 皮肤**：proto-first，**零 protoc**。每个 service / rpc / 参数 / 错误都与 store 既有的 schema / GQL / RBAC 语义一一对应——适配层零语义发明。

- **Node**——`store-grpc-node`（npm）：`@grpc/grpc-js` 运行时 + `protobufjs` **动态序列化**。**零 protoc**——无 `.proto` 编译步骤、无生成代码落盘。
- **Python**——`store-grpc-py`（PyPI）：`grpcio` + `grpcio-tools`，proto 由内存文本**运行时编译**。**零手工 protoc**——你从不自己跑 `protoc`。
- **共享**——`spec/`（proto 生成 / 执行映射 / 错误与上下文的唯一事实源）+ `conformance/`（跨运行时用例，双端 runner 断言同一份 JSON）。

设计规则（三条铁律，见 `spec/00-overview.md`）：

1. **零语义发明**——service / rpc / 参数 / 错误 / 权限必须能映射到 store 已有语义；store 没有的，本层不提供。
2. **双端 parity 先行**——任何变更先改 `spec/`，再双端同步实现，再补 conformance 用例；规范 / 代码 / 用例不一致即为缺陷。
3. **禁掩盖错误**——store 抛出的任何错误都原样映射为带语义的 gRPC 错误（`status` + `details` + `store-error-code` trailer）。禁静默成功、禁回退默认值、禁把中性结果当错误。

> English index: [README.md](./README.md)

## 运行时矩阵

| | Node | Python |
|---|---|---|
| 包 | `store-grpc-node`（npm） | `store-grpc-py`（PyPI），`import store_grpc` |
| gRPC 承载 | `@grpc/grpc-js`——*可选*依赖 | `grpcio`——*可选* extra（`[grpc]`） |
| 序列化 | `protobufjs` 解析内存里的 `.proto` 文本，**动态**编解码 | `grpcio-tools`（`grpc_tools.protoc`）在**运行时**把内存 `.proto` 文本编译为 descriptor set，再构造消息类 |
| protoc | **零 protoc**——库从不调用 `protoc` / `grpcurl` | **零手工 protoc**——`grpc_tools.protoc` 进程内运行，用户从不调用 |
| 生成代码落盘 | 无 | 无 |
| 公开入口 | `buildProto` / `exportProto` / `createServer` | `build_proto` / `export_proto` / `create_server` |
| 服务句柄 | `Promise<{ server, proto, port, shutdown() }>` | `GrpcServer`：`.server` / `.pb` / `.proto` / `.port` / `.stop(grace)` |
| 错误模块 | `mapStoreError`（纯函数） | `store_grpc.errors.map_store_error`（纯函数） |
| 最小运行时 | Node >= 18（`engines`） | Python >= 3.10（`requires-python`） |
| 当前版本 | `0.2.1` | `0.2.1` |

同一份 schema，两端生成的 `.proto` **逐字节一致**，并对同一份 `conformance/cases/*.json` 断言行为一致。

## 目录

1. [它是什么](#它是什么)
2. [生成模型](#生成模型)
3. [何时使用](#何时使用)
4. [何时不要用](#何时不要用)
5. [安装](#安装)
6. [快速上手](#快速上手)
   - [Node](#快速上手node)
   - [Python](#快速上手python)
7. [Proto 参考](#proto-参考)
8. [执行映射](#执行映射)
9. [错误与上下文](#错误与上下文)
10. [场景事例](#场景事例)
11. [与其它协议皮的关系](#与其它协议皮的关系)
12. [v0 范围与路线](#v0-范围与路线)
13. [FAQ](#faq)
14. [开发](#开发)
15. [相关项目](#相关项目)
16. [License](#license)

## 它是什么

`store-grpc` 是 common-store 数据层之上的 **gRPC 适配层**。它不是新的数据层，除了「gRPC ↔ store 调用」的翻译之外不做任何事——不做鉴权、不做限流、不做缓存。

```
gRPC 客户端
   │  protobuf（package store.v0）
store-grpc（本仓库：grpc-js 适配器 / grpcio 适配器）
   │  store 原生 API：query / queryOne / insert / update / remove + setContext
数据层（nodejs-store / py-store / go-store / rust-store）
   │  GQL → 命令规划
rust-store core（解析 / 权限 / 方言）
   │
数据库（MongoDB / MySQL / PostgreSQL / SQLite）
```

store 实例与 REST、GraphQL 皮肤共享——同一份 JSON schema、同一套 GQL、同一套 RBAC。与 REST/GraphQL 唯一的本质差异是：**gRPC 是静态 IDL 协议，server 端必须有 `.proto`**。本仓库用 **proto-first / 双出口** 应对：

- `buildProto(store, opts)` / `build_proto(store, ...)` → `.proto` 文本（IDL 事实源）。**纯构建，零 gRPC 依赖**——永远可用。
- `exportProto(store, opts)` / `export_proto(store, ...)` → 同一份文本，命名对齐 store-graphql 的 `exportSDL` / `export_sdl`；这是你落盘给外部 `protoc` / `grpcurl` / 客户端 codegen 用的产物。
- `createServer(store, opts)` / `create_server(store, ...)` → 运行中的 gRPC server。它用**同一份 proto 文本**做运行时序列化注册（Node：`protobufjs.parse` + 手搭 grpc-js `ServiceDefinition`；Python：进程内 `grpc_tools.protoc` + generic handler）。**零 protoc 步骤、零生成代码落盘。**

## 生成模型

1. **生成源唯一**——store 的纯 JSON schema（`store.list()` / `store.get(name)`），与 REST / GraphQL 皮肤消费的完全一致。
2. **生成面**——每模型一个 service、固定五段 rpc：
   `List{Name}` / `Get{Name}` / `Create{Name}` / `Update{Name}` / `Delete{Name}`。
   模型注记：`"x-grpc": {"hidden": true}`（整个 service 不生成）与 `"x-grpc": {"readonly": true}`（只生成查询两段）。归档表过滤（`XxxDeleted` 在 `Xxx` 也在列时跳过）。
3. **JSON string 承载（v0）**——入参字段 `q`（GQL 余部，与 REST `?q=` 同源）+ `params_json`；出参字段 `data_json`。理由（记录于 `spec/01-proto-generation.md`）：GQL 查询串与 params 本就是动态 JSON，用 string 承载**零歧义、跨端 parity 成本最低**；逐字段强类型 message 是 v1 演进方向。
4. **错误**——REST 皮肤的状态码映射转为 gRPC 的 **status code + details + `store-error-code` trailer**（`INVALID_PARAM`、`INVALID_BODY`、`GQL_PARSE`、`CONTEXT_ERROR`、`ERR_PERMISSION`、`no_context` → `PERMISSION_DENIED`、`NOT_FOUND`，其余 → `INTERNAL`）。见[错误与上下文](#错误与上下文)。
5. **双出口、同一份文本**——`buildProto()` / `exportProto()` 给你 `.proto`；`createServer()` 在运行时注册同一份文本。库永远不写 `.pb.go` / `_pb2.py` / 生成的 JS。
6. **语言命名惯例（显式声明，非 parity 缺陷）**——`.proto` 字段名恒为 `snake_case`（`params_json` / `body_json` / `set_json` / `data_json`）。各语言按其 protobuf 惯例呈现：Python `_pb2` 用 `body_json`；Node（protobufjs）用 `bodyJson`；Go 生成代码用 `BodyJson`。适配层不做跨惯例改写。

## 何时使用

- **强类型、契约优先的内部服务调用**——`.proto` 即接口契约。
- **一份产物、多语言客户端**——从单一导出的 `.proto` 生成 Go / Java / C++ / Python / Node 客户端。
- **长连接 / 高吞吐读**——用 HTTP/2 多路复用 + 二进制帧，取代每次请求的 JSON-over-HTTP。
- **独立端口 / 模型子集**——把一份精选的 `resources` 列表暴露在独立端口，与 REST/GraphQL 面隔离。
- **跨语言一致的错误语义**——每种语言看到相同的 `status` 码与相同的机器可读 `store-error-code`。
- **你已用 nodejs-store / py-store / go-store**，想加一层 gRPC 又不想手写 `.proto`。

## 何时不要用

- **今天就要逐字段强类型 message / 生成的 DTO**——v0 用 JSON string 承载，强类型是 v1。
- **需要流式**——v0 只有 unary（每个 rpc 都是 `requestStream: false`、`responseStream: false`）。流式在路线图上。
- **需要 server reflection**（不带 `.proto` 就用 `grpcurl`）——v0 没有；请导出 `.proto` 并用 `-proto` 传入。reflection 在路线图上。
- **想要浏览器原生客户端且无代理**——gRPC over HTTP/2 不能被浏览器直接调用；gRPC-Web 不在范围内。
- **想让适配层做鉴权 / 限流 / 缓存**——按设计它一件都不做，这些属于宿主或网关。

## 安装

### Node

```bash
npm i store-grpc-node
# 承载需要 @grpc/grpc-js（声明为 optional dependency；若你的包管理器跳过 optional 依赖，请显式安装）：
npm i @grpc/grpc-js
# nodejs-store 是 optional peer dependency——在持有 store 的应用里安装：
npm i nodejs-store
```

- `protobufjs` 是常规依赖（用于 `.proto` 文本与序列化）。
- `buildProto` / `exportProto` **完全不依赖 gRPC 库**即可用；只有 `createServer` 需要 `@grpc/grpc-js`。

### Python

```bash
pip install store-grpc-py            # 仅构建/导出：零运行时依赖
pip install "store-grpc-py[grpc]"    # 承载：grpcio + grpcio-tools
pip install py-store                 # 持有 store 的宿主
```

- `build_proto` / `export_proto` **不装 `grpcio`** 即可用。
- `create_server` 延迟导入 `grpcio` + `grpcio-tools`；缺失时抛带安装指引的错误，绝不静默降级。

## 快速上手

### 快速上手（Node）

```js
// server.js  — 运行：node server.js
const fs = require('node:fs');
const { init, store } = require('nodejs-store');
const { exportProto, createServer } = require('store-grpc-node');

async function main() {
  await init({ default: 'mongodb://127.0.0.1:27017/app' });
  store.register({
    name: 'User',
    description: '用户表：平台账号主档',
    fields: {
      _id: { type: 'string' },
      name: { type: 'string' },
      age: { type: 'int' },
    },
  });

  // 1) 产出 .proto——喂 grpcurl / protoc / 客户端 codegen。
  fs.writeFileSync('store_grpc.proto', exportProto(store));

  // 2) 承载同一份 schema——动态序列化，零 protoc。
  const { port, proto, shutdown } = await createServer(store, { port: 50051 });
  console.log(`store-grpc-node listening on 127.0.0.1:${port}`);
  // Service: store.v0.User -> ListUser / GetUser / CreateUser / UpdateUser / DeleteUser

  process.on('SIGINT', async () => { await shutdown(); process.exit(0); });
}

main();
```

用 `grpcurl` 调用（导出的 `.proto` 即客户端契约）：

```bash
# Create
grpcurl -plaintext -proto store_grpc.proto \
  -d '{"body_json": "{\"name\":\"alice\",\"age\":30}"}' \
  127.0.0.1:50051 store.v0.User/CreateUser
# -> {"dataJson": "{\"_id\":\"u1\",\"name\":\"alice\",\"age\":30}"}

# List（q = GQL 余部 + params_json）
grpcurl -plaintext -proto store_grpc.proto \
  -d '{"q": "($condition: @c0) { name, age }", "params_json": "{\"c0\":{\"age\":30}}"}' \
  127.0.0.1:50051 store.v0.User/ListUser
# -> {"dataJson": "[{\"_id\":\"u1\",\"name\":\"alice\",\"age\":30}]"}

# Get
grpcurl -plaintext -proto store_grpc.proto \
  -d '{"id": "u1"}' \
  127.0.0.1:50051 store.v0.User/GetUser
# -> {"dataJson": "{\"_id\":\"u1\",\"name\":\"alice\",\"age\":30}"}
```

在自己的 Node 客户端里用生成的 stub（grpc-js + protobufjs，无 codegen）：

```js
const grpc = require('@grpc/grpc-js');
const protobuf = require('protobufjs');
const { createServer } = require('store-grpc-node');

// createServer() 之后，句柄上已经带有 proto 文本：
const { port, proto } = await createServer(store, { port: 0 });
const root = protobuf.parse(proto).root;
const ListRequest = root.lookupType('store.v0.ListRequest');
const StoreReply = root.lookupType('store.v0.StoreReply');

const methods = {
  ListUser: {
    path: '/store.v0.User/ListUser',
    requestStream: false, responseStream: false,
    requestSerialize: (o) => Buffer.from(ListRequest.encode(ListRequest.fromObject(o)).finish()),
    requestDeserialize: (b) => ListRequest.toObject(ListRequest.decode(b)),
    responseSerialize: (o) => Buffer.from(StoreReply.encode(StoreReply.fromObject(o)).finish()),
    responseDeserialize: (b) => StoreReply.toObject(StoreReply.decode(b)),
  },
};
const Client = grpc.makeGenericClientConstructor(methods, 'User');
const client = new Client(`127.0.0.1:${port}`, grpc.credentials.createInsecure());
client.ListUser({ q: ' { name, age }' }, (err, res) => {
  if (err) throw err;
  console.log(JSON.parse(res.dataJson));
});
```

### 快速上手（Python）

```python
# server.py  — 运行：python server.py
import asyncio

from py_store import init, store
from store_grpc import export_proto, create_server

async def main():
    await init({'default': 'mongodb://127.0.0.1:27017/app'})
    store.register({
        'name': 'User',
        'description': '用户表：平台账号主档',
        'fields': {
            '_id': {'type': 'string'},
            'name': {'type': 'string'},
            'age': {'type': 'int'},
        },
    })

    # 1) 产出 .proto（与 Node 对同一 schema 产出的文本一致）。
    with open('store_grpc.proto', 'w', encoding='utf-8') as f:
        f.write(export_proto(store))

    # 2) 承载它——grpcio server，proto 运行时进程内编译（零手工 protoc）。
    server = create_server(store, port=50051)
    print('store-grpc-py listening on 127.0.0.1:%d' % server.port)
    try:
        await asyncio.Event().wait()          # 保持运行
    finally:
        server.stop(None)

asyncio.run(main())
```

用服务句柄上暴露的编译产物 `server.pb` 调用，或用同一份 `store_grpc.proto` 交给 `grpcurl`：

```bash
grpcurl -plaintext -proto store_grpc.proto \
  -d '{"q": "($condition: @c0) { name, age }", "params_json": "{\"c0\":{\"age\":30}}"}' \
  127.0.0.1:50051 store.v0.User/ListUser
```

```python
import grpc
from store_grpc import create_server

server = create_server(store, port=0)          # 0 -> 随机端口
channel = grpc.insecure_channel(f'127.0.0.1:{server.port}')
unary = channel.unary_unary(
    '/store.v0.User/ListUser',
    request_serializer=server.pb.ListRequest.SerializeToString,
    response_deserializer=server.pb.StoreReply.FromString,
)
reply = unary(server.pb.ListRequest(q=' { name, age }'))
print(reply.data_json)
channel.close()
```

> 生产中用 `create_server(store, port=50051)` 绑定并返回；退出时调用 `server.stop(None)`。

## Proto 参考

下面是 `exportProto` / `export_proto` 对 `User` 模型（字段 `_id`、`name`、`age`）产出的确切文本（这是真实的生成产物，见 `node/test/tmp-proto-check/store_grpc.proto`）：

```proto
// 由 store-grpc 自动生成 —— 事实源：store 的 JSON schema（与 store-api / store-graphql 同源）
syntax = "proto3";

package store.v0;

message ListRequest {
  string q = 1;           // 完整 GQL 查询串（自模型名始）；空串 ⇒ 服务端补 schema 投影
  string params_json = 2; // GQL params（如 {"c0":{"age":{"$gte":18}}}）的 JSON 编码
}

message GetRequest     { string id = 1; }

message CreateRequest  { string body_json = 1; }

message UpdateRequest  { string id = 1; string set_json = 2; }

message DeleteRequest  { string id = 1; }

message StoreReply     { string data_json = 1; }

// 用户表：平台账号主档
service User {
  // 列表查询：q 为完整 GQL 查询串（自模型名始，与 REST ?q= 同源）；空串 ⇒ 服务端按 schema 投影
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
```

要点：

- **package** 固定为 `store.v0`（两端常量 `PACKAGE`）。所有 message / service 都在其下。
- **service 名** = 模型名首字母大写、其余原样（`user` → `User`，`user_profile` → `User_profile`）。
- **rpc 名** = 动词前缀（`List` / `Get` / `Create` / `Update` / `Delete`）+ service 名。
- **`opts.resources`**（`createServer`）/ `resources=`（`create_server`）把生成限定到显式列表；缺省取 `store.list()` 并过滤归档表。
- **`opts.idField`** / `id_field` 设定 `Get` / `Update` / `Delete` 用的主键字段；两端缺省 `_id`。
- **description 透传**：模型级 `description` 写入 service 的 `//` 注释（见上方 `// 用户表：平台账号主档`）；rpc 注释为固定文案。适配层透传不改写。
- **`q` 语义**：操作性契约（`spec/01`、`spec/02` 与两端实现）是 `q` 为**模型名之后**的 GQL 余部——适配层求值 `store.query(name + q, params)`（`node/src/index.js`、`py/src/store_grpc/server.py`），conformance 用例 `users-crud.json` 传入 `"q": "($condition: @c0) { name, age }"`。生成 `.proto` 里展示的行内注释把拼接后的形态说得较宽松；以「余部」形态为准。
- **语言惯例**：`.proto` 字段保持 `snake_case`；Python `_pb2` 暴露 `body_json`，Node/`protobufjs` 暴露 `bodyJson`，Go codegen 暴露 `BodyJson`（`spec/01`）。
- **`x-grpc` 注记是 schema 级的，不写进 `.proto`**：

  | 注记 | 效果 |
  |---|---|
  | `"x-grpc": {"hidden": true}` | 整个 service **不生成** |
  | `"x-grpc": {"readonly": true}` | 只生成 `List` / `Get` 两段 rpc |

- **归档表过滤**：模型 `XxxDeleted` 在 `Xxx` 也在模型列表时被跳过（与 store-api / store-graphql 逐字一致）。
- **重名守卫**：两个模型名首字母大写后相同（`user` + `User`）⇒ 构建期抛错、前缀 `ERR_NAME_CONFLICT:`——绝不静默合并。

## 执行映射

所有 rpc 的 handler 遵循同一骨架：解析请求 JSON → 调 store 原生 API → 成功结果 `JSON.stringify` 进 `StoreReply.data_json`；任何错误经 `spec/03` 判定链映射后终止调用。

以模型 `User`、service `User`、`id_field = _id`、schema 投影 `proj`（` { name, age }` 形状）为例：

| rpc | 请求 | store 调用 | 成功语义 |
|---|---|---|---|
| `ListUser` | `q`、`params_json` | `store.query(name + (q or proj), JSON.parse(params_json))` | 数组 JSON |
| `GetUser` | `id` | `store.queryOne('User($condition: @c0)' + proj, { c0: { _id: id } })` | 对象 JSON；`null` ⇒ `NOT_FOUND` |
| `CreateUser` | `body_json` | `store.insert('User', JSON.parse(body_json))` | 插入文档 JSON |
| `UpdateUser` | `id`、`set_json` | `store.update('User', { _id: id }, JSON.parse(set_json))` | `store.update` 返回值原样（对齐 REST `PATCH`，不做二次回读） |
| `DeleteUser` | `id` | `store.remove('User', { _id: id })` | `store.remove` 返回值原样 |

**请求 JSON 解析规则**（`spec/02-execution-mapping.md`）：

| 字段 | 规则 | 违例 |
|---|---|---|
| `params_json` | 空串 ⇒ params 为 `undefined`（不传）；否则必须是合法 JSON | ⇒ `INVALID_ARGUMENT` + `INVALID_PARAM` |
| `body_json` | 必须是 JSON **对象**编码（数组 / 标量 / 非法 JSON 拒绝） | ⇒ `INVALID_ARGUMENT` + `INVALID_BODY` |
| `set_json` | 同 `body_json` | ⇒ `INVALID_ARGUMENT` + `INVALID_BODY` |
| `q` | 空串 ⇒ 服务端拼 `name + proj`；非空 ⇒ 服务端拼 `name + q`（模型名之后的 GQL 余部，**与 REST `?q=` 完全同源**） | core 抛 `ERR_GQL_PARSE:` ⇒ `spec/03` 判定链 |
| `id` | 原样透传为定位值，服务端不做类型改写 | — |

**schema 投影（`proj`）**：从 store 元数据生成显式投影串 ` { f1, f2 }`（fields **与** computes 均视为字段，与 store-graphql 对齐）。取值顺序：`store.get(name)`（nodejs 形态）→ py-store 的 `schema` 模块（当 `Store` 类未暴露 `get` 时）。两者皆不可得 / fields 为空 ⇒ 空串（data 仅 `_id`——上游 schema 定义不完整的**显式后果**，与 REST 同语义）。仅 `ListUser` 在 `q` 缺失时用 `proj`；`GetUser` 恒用 `proj`。

**`limit` 守卫：本皮肤不设。** GraphQL 的缺省 50 / 上限 1000 属其 spec；REST 与 gRPC 的 `q` 透传不做行数守卫（core 的行数封顶在 text2query 档生效）。本皮肤与 REST 对齐。

**上下文注入（`contextProvider`）**（`spec/02`）：

- `opts.contextProvider`（Node）/ `context_provider=`（Python）是每 rpc 调用前的钩子，收到本次调用的元数据（Node：grpc-js `call.metadata`；Python：`ServicerContext.invocation_metadata()`）。
- 返回值经 `store.setContext(ctx)` 注入。返回 `null`/`undefined` 时同样**显式**注入（`setContext(null)`）——清除语义必须落地，有状态持有的运行时禁止残留上一请求上下文，防身份跨请求泄漏。
- 钩子抛错 ⇒ `spec/03` 判定链：`PermissionError` ⇒ `PERMISSION_DENIED`；其余 ⇒ `UNAUTHENTICATED`，原 message 原样透传。

**server API**：

- Node：`createServer(store, opts) -> Promise<{ server, proto, port, shutdown() }>`。给了 `opts.port` 时在 `bindAsync` 完成后 resolve（`port: 0` ⇒ 随机端口）；未给 `port` 时仅注册不监听。
- Python：`create_server(store, *, port=None, host='127.0.0.1', resources=None, id_field='_id', context_provider=None, permission_error=None) -> GrpcServer`（同步；`.port` / `.stop(grace)`，与 Node 同构）。
- 承载依赖两端均为 optional：node 缺 `@grpc/grpc-js` / py 缺 `grpcio`+`grpcio-tools` 时 `createServer` / `create_server` 抛**带安装指引**的错误；`buildProto` / `exportProto` / `build_proto` / `export_proto` 永远可用。
- Python 侧有 sync-`grpcio` 与 async-`py-store` 的双兼容：py-store 实例方法为 `async`，适配层做 awaitable 检测——awaitable 在工作线程内经私有事件循环执行，同步返回值直接使用。两种宿主形态行为一致，适配层不做语义改写。

## 错误与上下文

REST 的错误是 HTTP 状态码 + JSON body；gRPC 的错误承载为三件套（`spec/03-errors-context.md`）：

1. **status code**（`grpc.StatusCode`）
2. **details**——原 message 透传；取不到则空串（**禁伪造**）
3. **trailer metadata** `store-error-code`——稳定机器码（与 REST 的 `error.code` 同码表）

**状态码映射表：**

| `store-error-code` | gRPC status | REST | 触发 |
|---|---|---|---|
| `INVALID_PARAM` | `INVALID_ARGUMENT` | 400 | `params_json` 非法 JSON |
| `INVALID_BODY` | `INVALID_ARGUMENT` | 400 | `body_json` / `set_json` 非对象或非法 JSON |
| `GQL_PARSE` | `INVALID_ARGUMENT` | 400 | core 抛 `ERR_GQL_PARSE:`（details 剥前缀取原文） |
| `CONTEXT_ERROR` | `UNAUTHENTICATED` | 401 | `contextProvider` 抛非权限类错误 |
| `ERR_PERMISSION` | `PERMISSION_DENIED` | 403 | `PermissionError` 类 / `ERR_PERMISSION:` 前缀 |
| `no_context` | `PERMISSION_DENIED` | 403 | 宿主 `NoContextError`（machine code `no_context`）/ core `ERR_NO_CONTEXT:` 前缀（requireContext 开启且 ctx 缺失，同属权限类） |
| `NOT_FOUND` | `NOT_FOUND` | 404 | `queryOne` 空结果 |
| `err.code` / 类名 | `INTERNAL` | 500 | 其余一切错误（码取 `err.code`，缺省类/类型名，再缺省 `STORE_ERROR`） |

**判定顺序（双端一致；改动必须先改 spec）：**

```
适配层守卫（INVALID_PARAM / INVALID_BODY / NOT_FOUND / CONTEXT_ERROR）
  -> PermissionError 类判定（store.PermissionError 可得时按类型，禁按文案匹配）
  -> ERR_PERMISSION: 前缀判定（core 稳定前缀契约）
  -> NoContext（machine code `no_context` / ERR_NO_CONTEXT: 前缀，权限类同档）-> PERMISSION_DENIED
  -> ERR_GQL_PARSE: 前缀判定（details 剥前缀取原文）
  -> 其余 -> INTERNAL 透传
```

**禁掩盖错误：**

- store 抛出的 message **原样透传**进 details；取不到置空串——禁伪造文案。
- 禁 catch 后洗成成功、禁回退默认值、禁把中性结果当错误。
- 成功的 `StoreReply` 中不得出现错误字段（错误只经 gRPC status 通道）。

**上下文（metadata 语义）：**

- 请求元数据键名不做大小写改写（gRPC metadata 本身小写化，适配层原样交给 provider）。
- 权限拒绝必须在**进 handler 语义之前**判定——`contextProvider` 抛错时 store 不发生调用（smoke 测试断言 `store.gqlLog.length` 不变）。

## 场景事例

每个场景遵循：**业务背景 → 调用 → 响应 → 优势所在。**

### 场景 1——微服务间 CRUD

**业务背景。** `billing` 服务需要读写 `accounts` 服务持有的 `User` 记录。两者都是内部服务，都不面向浏览器。与其另写一套 REST 客户端，`accounts` 直接从它已经注册的同一个 store 导出 `.proto`，`billing` 调用生成的 service。

**调用。**

```bash
grpcurl -plaintext -proto store_grpc.proto \
  -d '{"body_json": "{\"name\":\"bob\",\"age\":41}"}' \
  accounts.internal:50051 store.v0.User/CreateUser

grpcurl -plaintext -proto store_grpc.proto \
  -d '{"id": "u2"}' \
  accounts.internal:50051 store.v0.User/GetUser
```

**响应。**

```json
{"dataJson": "{\"_id\":\"u2\",\"name\":\"bob\",\"age\":41}"}
```

**优势所在。** 契约是 `.proto` 而不是 wiki 上的散文——`billing` 无法发送不存在的字段，且线路上是二进制载荷。服务端就是 accounts 的 REST/GraphQL 面所用的**同一个** store 实例，不存在第二条需要保持一致的数据路径。

### 场景 2——一份 `.proto`、多语言客户端

**业务背景。** 一个 Go 数据管道、一个 Java 批处理任务、一个 Node 看板都要查询 `User`。与其手写三套 HTTP 客户端，团队从单一产物生成客户端。

**调用（先产出产物，再 codegen）。**

```bash
# Node：从 store 写出 .proto
node -e "const {exportProto}=require('store-grpc-node');const fs=require('fs');\
fs.writeFileSync('store_grpc.proto', exportProto(require('nodejs-store').store))"

# 交给任意 protoc 插件 / 客户端生成器（Go、Java、Python、C++ ...）：
protoc -I. --go_out=. --go-grpc_out=. store_grpc.proto
```

**响应。** 每个生成的客户端调用同一批 rpc：

```
store.v0.User/ListUser   store.v0.User/GetUser    store.v0.User/CreateUser
store.v0.User/UpdateUser store.v0.User/DeleteUser
```

**优势所在。** `.proto` 是唯一的接口产物；你唯一要跑的就是 `exportProto` / `export_proto`，它和服务端序列化用的是**同一份文本**——客户端与服务端在 message 形状上永不会漂移。

### 场景 3——长连接、高吞吐读

**业务背景。** 一个读密集的看板每分钟轮询 `User` 上千次。每次轮询都新建 HTTP 连接会浪费握手。

**调用。**

```js
// 进程存活期内复用同一个 grpc-js channel。
const client = new Client(`127.0.0.1:${port}`, grpc.credentials.createInsecure());
setInterval(() => {
  client.ListUser({ q: ' { name, age }' }, (err, res) => { /* ... */ });
}, 50);
```

**响应。** `{"dataJson":"[...]"}`——单条多路复用的 HTTP/2 流承载每一次调用。

**优势所在。** gRPC 跨调用保持 channel（及 HTTP/2 连接）打开，用二进制帧在一条连接上多路复用并发请求——比每次轮询重建 HTTP、重新解析 JSON body 的每调用开销低得多。

### 场景 4——独立端口 / 精选模型子集

**业务背景。** 内部管理工具需要 `User` 与 `AuditEvent`，但公开 REST 面还暴露了 `Order`、`Payment`、`Cart`。团队不希望管理工具的 gRPC 端点到任何支付模型。

**调用。**

```js
const { createServer } = require('store-grpc-node');
// 此端口上只有这两个 service——从同一个 store 生成。
await createServer(store, { resources: ['User', 'AuditEvent'], port: 50052 });
```

```bash
grpcurl -plaintext -proto admin.proto -d '{}' 127.0.0.1:50052 store.v0.User/ListUser   # OK
grpcurl -plaintext -proto admin.proto -d '{}' 127.0.0.1:50052 store.v0.Payment/ListPayment
# ERROR: Code: Unimplemented ...
```

**响应。** `ListUser` 成功；`ListPayment` 返回 `UNIMPLEMENTED`，因为该 service 从未在此端口注册。

**优势所在。** `resources` 按 server 精选生成的产物面，于是独立端口（或独立进程）可以只暴露消费者被允许看到的模型——无需第二份 schema，也不在 IDL 里泄漏无关模型名。

### 场景 5——与 REST、GraphQL 并存

**业务背景。** 一个平台给公开客户端提供 REST、给 Web 应用提供 GraphQL、给内部服务提供 gRPC——都跑在同一份 store 和同一份 schema 之上。

**调用。**

```js
// store-gateway 声明式组合三层皮肤（对全部皮肤 optional 依赖，逐层 `enabled` 开关）：
const { createGateway } = require('store-gateway');
await createGateway(store, {
  rest:     { enabled: true, port: 8080 },
  graphql:  { enabled: true, port: 4000 },
  grpc:     { enabled: true, port: 50051 },
});
```

**响应。** 同一个 `User` 模型可通过 `GET /users?q=...`（REST）、GraphQL 字段（GraphQL）与 `store.v0.User/ListUser`（gRPC）访问——返回同样的记录。

**优势所在。** 三层皮肤是零互相依赖的独立包，gateway 只做接线。你可以先上 REST 之后再加 gRPC 而不动 schema，或单独运行任意一层（gateway 是可选的）。

### 场景 6——跨语言保持一致的错误语义

**业务背景。** Go、Python、Node 三个调用方都对一个已删除记录调用 `GetUser`。每个都要能**程序化地**区分「不存在」「无权限」「服务端缺陷」——而不是去字符串匹配 message。

**调用。**

```bash
grpcurl -v -plaintext -proto store_grpc.proto \
  -d '{"id": "u1"}' 127.0.0.1:50051 store.v0.User/GetUser
```

**响应。**

```
ERROR:
  Code: NotFound
  Message: 记录不存在: _id=u1
Trailers:
  store-error-code: NOT_FOUND
```

**优势所在。** `status` 码是粗分类，`store-error-code` trailer 是稳定机器码，`details` 原样携带 message。三种语言看到相同的 `status` + trailer，于是调用方按码分支而非按本地化文案——且适配层绝不把 store 错误藏在一个笼统的「internal」后面（见[错误与上下文](#错误与上下文)码表）。

### 场景 7——彻底隐藏内部模型

**业务背景。** 模型 `SecretLog` 存有凭据材料，绝不能经 gRPC 触达（它在 store 中仍存在，供 REST 以另一套护栏访问）。

**调用。**

```js
store.register({
  name: 'SecretLog',
  'x-grpc': { hidden: true },   // schema 级注记
  fields: { /* ... */ },
});
```

```bash
grpcurl -plaintext -proto store_grpc.proto -d '{}' 127.0.0.1:50051 store.v0.SecretLog/ListSecretLog
# ERROR: Code: Unimplemented ...  （service 从未生成）
```

**响应。** 导出的 `.proto` 中不存在 `service SecretLog`；该 rpc 为 `UNIMPLEMENTED`。

**优势所在。** 暴露控制在 schema 层、与模型定义同处一处，且对每种语言、每份生成产物都一致——隐藏模型根本不会出现在 IDL 中。

### 场景 8——只读模型

**业务背景。** `AuditEvent` 仅由内部管道写入；读者需要经 gRPC 查询它，但绝不能经本面 create / update / delete。

**调用。**

```js
store.register({
  name: 'AuditEvent',
  'x-grpc': { readonly: true },
  fields: { _id: { type: 'string' }, actor: { type: 'string' }, at: { type: 'string' } },
});
```

```bash
grpcurl -plaintext -proto store_grpc.proto -d '{"q":" { actor }"}' \
  127.0.0.1:50051 store.v0.AuditEvent/ListAuditEvent            # OK
grpcurl -plaintext -proto store_grpc.proto -d '{"body_json":"{}"}' \
  127.0.0.1:50051 store.v0.AuditEvent/CreateAuditEvent         # ERROR: Unimplemented
```

**响应。** 生成的 service 只含 `ListAuditEvent` 与 `GetAuditEvent`。

**优势所在。** 只读访问在 schema 一次声明、在协议面强制——根本不存在可供误调的 `Create`/`Update`/`Delete` rpc，只读消费者无法经 gRPC 改动该表。

## 与其它协议皮的关系

`store-grpc` 是同一份 store 之上四层协议皮之一，外加一个组合器：

| 皮肤 | 形态 | 包装 |
|---|---|---|
| [store-api](https://github.com/coenddt/store-api) | REST（HTTP 状态码 + JSON body） | REST |
| [store-graphql](https://github.com/coenddt/store-graphql) | GraphQL SDL | GraphQL |
| **store-grpc**（本仓库） | 每模型一个 gRPC service | gRPC |
| store-mcp | MCP 工具面 | MCP |
| [store-gateway](https://github.com/coenddt/store-gateway) | 声明式组合器 | — |

- 各皮肤**架构独立**：独立仓库、独立包（npm / PyPI）、代码零互相依赖。唯一共享的是 store 实例（一份 JSON schema 事实源）与各自 skin 的 `spec/` + `conformance/`。
- 各皮肤经 `store-gateway` **可配置插拔组合**（对全部皮肤 optional 依赖，逐层 `enabled` 开关）。单用某层皮肤时完全不需要 gateway。
- 映射血缘：gRPC 的 `q` 与 REST `?q=` 完全同源；service/rpc 命名对齐 GraphQL 的「每模型一类型」视角；`x-grpc` 注记与 `x-graphql` 对齐；错误码表与 REST 相同。

## v0 范围与路线

**v0（现状，`0.2.1`）：**

- 运行时：Node（`store-grpc-node`）+ Python（`store-grpc-py`）。
- 每模型五段 CRUD rpc（`List` / `Get` / `Create` / `Update` / `Delete`），仅 unary。
- JSON string 承载（入参 `q` + `params_json`，出参 `data_json`）。
- proto-first：`buildProto` / `exportProto`（及 Python 同名出口），运行时用同一份文本序列化；零生成代码落盘。
- 错误映射：gRPC status + details + `store-error-code` trailer。
- 上下文经 metadata（`contextProvider` / `context_provider`），含显式 `setContext(null)` 清除。
- schema 级 `x-grpc` 注记：`hidden`、`readonly`；归档表过滤；PascalCase 冲突守卫。

**路线：**

- **v1**——`go` / `rust` 宿主；schema 反射逐字段强类型 message；可选 server reflection（`grpcurl` 免 `.proto` 文件）。
- 流式 rpc（`requestStream` / `responseStream`）不在 v0——每个 rpc 都是 unary。

**spec 边界：** 行为由 `spec/00-overview.md`、`spec/01-proto-generation.md`、`spec/02-execution-mapping.md`、`spec/03-errors-context.md` 定义。规范 / 代码 / conformance 用例必须一致；不一致即为缺陷。

## FAQ

1. **客户端需要 `.proto` 文件吗？** 需要。gRPC 是静态 IDL 协议，服务端必须发布契约。用 `exportProto` / `export_proto` 取到它。v0 没有 server reflection，因此 `grpcurl` 必须传 `-proto store_grpc.proto`。
2. **为什么用 JSON string 而不是强类型 message？** 这是记录在 `spec/01` 里的 v0 决策：GQL 查询串与 params 本就是动态 JSON，用 string 承载零歧义、跨端 parity 成本最低。强类型 message 是 v1 项，须先改 spec。
3. **有行数守卫吗（像 GraphQL 的 50/1000）？** 没有。GraphQL 的守卫属其 spec；REST 与 gRPC 的 `q` 透传不做行数守卫（core 的行数封顶在 text2query 档）。本皮肤与 REST 对齐。
4. **怎么拿到新建记录的 id？** 从 `Create*` 响应的 `data_json`（插入的文档，含 `_id`）。
5. **`Get` 无匹配时返回什么？** `NOT_FOUND`：status 为 `NOT_FOUND`、`store-error-code: NOT_FOUND`、details 形如 `记录不存在: _id=u1`。它绝不返回空成功。
6. **`Update` 会二次回读记录吗？** 不会。它返回 `store.update` 的原始返回值，对齐 REST `PATCH`（`spec/02`）——没有第二次读。
7. **身份 / 鉴权怎么传？** 经请求 metadata，由你的 `contextProvider` / `context_provider` 转换为经 `store.setContext` 注入的上下文。返回 `null` 显式清除上下文。权限类错误变为 `PERMISSION_DENIED`；其余钩子错误变为 `UNAUTHENTICATED`。
8. **为什么 Python 里字段是 `data_json`、Node 里却是 `dataJson`？** `.proto` 字段是 `snake_case`；protobufjs 暴露 camelCase JS 属性，`_pb2` 保留 snake_case，Go codegen 用 PascalCase。这是显式声明的语言惯例（`spec/01`），非 parity 缺陷——按目标语言惯例访问字段。
9. **能只暴露部分模型，或放到独立端口吗？** 可以。传 `resources: ['User', 'Order']`（Python 为 `resources=[...]`）和/或不同 `port`；列表外的模型不生成，其 rpc 返回 `UNIMPLEMENTED`。
10. **两个模型名首字母大写后冲突怎么办？** 生成期抛前缀为 `ERR_NAME_CONFLICT:` 的错误；绝不静默合并。
11. **支持流式吗？** v0 不支持——每个 rpc 都是 unary（`requestStream: false`、`responseStream: false`）。流式在路线图上。
12. **需要装 `protoc` 吗？** 不需要。Node 用 `protobufjs`（无 `protoc`）；Python 用 `grpcio-tools`，它在运行时进程内跑 `protoc`——你从不调用它。`buildProto` / `exportProto`（及 Python 同名出口）在完全没装 gRPC 库时也可用。

## 开发

```bash
# Node（mock store + 真实 @grpc/grpc-js server）——跑 smoke、error-matrix、conformance 三套
cd node && npm i && npm test

# Python（mock store + 真实 grpcio server）
cd py && pip install -e ".[dev]" && pytest
```

- Node 测试：`node --test` 跑 `node/test/*.test.js`（`smoke`、`error-matrix`、`conformance`）。
- Python 测试：`pytest` 跑 `py/tests/`（`test_smoke`、`test_error_matrix`、`test_conformance`）。
- 双端 runner 执行同一份 `conformance/cases/*.json`（`users-crud` 用例有 10 个步骤），断言行为逐项一致。
- `proto round-trip` 用例把导出文本落盘后再解析 / 再编译，证明该导出是合法 `.proto`。

贡献规则：**先改 `spec/`，再双端同步实现，最后补 / 改 conformance 用例。** 规范 / 代码 / 用例不一致即为缺陷。

## 相关项目

- [store-api](https://github.com/coenddt/store-api)——REST 皮肤。
- [store-graphql](https://github.com/coenddt/store-graphql)——GraphQL 皮肤。
- [store-gateway](https://github.com/coenddt/store-gateway)——各皮肤的声明式组合器。
- store-mcp——MCP 工具皮肤。
- nodejs-store / py-store / go-store / rust-store——数据层家族（宿主 + Rust core）。
- meta-store、create-store-app、store-rbac-bootstrap、store-resource-bootstrap——控制面、脚手架与引导插件。

## License

MIT——见 [LICENSE](./LICENSE)。
