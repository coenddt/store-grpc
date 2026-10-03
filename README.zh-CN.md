# store-grpc

为 common-store 数据层家族（nodejs-store / py-store / go-store）按 schema 自动生成**标准 gRPC** API——同一份 JSON schema 事实源之上的第三层皮肤（第一层 REST：[store-api](https://github.com/coenddt/store-api)，第二层 GraphQL：[store-graphql](https://github.com/coenddt/store-graphql)）。

- Node：`store-grpc-node`（npm）——grpc-js 运行时 + protobufjs 动态序列化，**零 protoc**
- Python：`store-grpc-py`（PyPI）——grpcio + grpcio-tools 运行时编译，**零手工 protoc**
- 共享：`spec/`（proto 生成 / 执行映射 / 错误与上下文的唯一事实源）+ `conformance/`（跨运行时一致性用例，双端 runner 断言同一份 JSON）

设计规则：**gRPC 只是 GQL 的又一层 RPC 皮肤** —— 适配层零语义发明；一切映射到 store 既有的 schema / GQL / RBAC 语义。proto-first 双出口：`buildProto()` / `exportProto()` 产出 `.proto`（IDL 事实源），同一份文本驱动运行时序列化——零生成代码落盘。

> English index: [README.md](./README.md)

## 快速上手

### Node（`store-grpc-node`）

```js
const { init, store } = require('nodejs-store');
const { exportProto, createServer } = require('store-grpc-node');

await init({ default: 'mongodb://...' });
store.register(defn);                     // 纯 JSON schema，注册即得

const proto = exportProto(store);         // .proto 文本：喂 protoc / grpcurl / 客户端 codegen
const { port, shutdown } = await createServer(store, { port: 50051 });
// 客户端：store.v0.User 的 stub（ListUser / GetUser / CreateUser / UpdateUser / DeleteUser）
```

### Python（`store-grpc-py`）

```python
from py_store import init, store
from store_grpc import export_proto, create_server

await init({'default': 'mongodb://...'})
store.register(defn)

proto = export_proto(store)               # .proto 文本
server = create_server(store, port=50051) # grpcio server，运行时编译
# grpcurl -plaintext -d '{"q": "($condition: @c0)", "params_json": "{...}"}' localhost:50051 store.v0.User/ListUser
```

## 自动生成的机制

1. **生成源唯一**：store 的纯 JSON schema（与 REST / GraphQL 皮肤同源）
2. **生成面**：每模型一个 service——`List{Name}` / `Get{Name}` / `Create{Name}` / `Update{Name}` / `Delete{Name}`；注记 `"x-grpc": {"hidden": true | "readonly": true}`；归档表过滤（`XxxDeleted` 且 `Xxx` 在列）
3. **JSON string 承载（v0）**：入参 `q`（模型名之后的 GQL 余部，与 REST `?q=` 同源）+ `params_json`，出参 `data_json`——零歧义、跨端 parity 成本最低
4. **错误**：HTTP 皮肤状态码映射 → gRPC status + details + `store-error-code` trailer（`INVALID_PARAM` / `INVALID_BODY` / `GQL_PARSE` / `CONTEXT_ERROR` / `ERR_PERMISSION` / `NOT_FOUND` / …），禁掩盖错误

## 与另两层皮肤的插拔组合

三层皮肤各自独立包、代码零互相依赖；用 [store-gateway](https://github.com/coenddt/store-gateway) 声明式组合（对三层全部 optional 依赖，逐层 `enabled` 开关插拔），或单用任意一层。

## 开发

```bash
node:    cd node && npm i && npm test                  # 10 用例（mock store + 真实 grpc-js server）
python:  cd py && pip install -e ".[dev]" && pytest    # 10 用例（mock store + 真实 grpcio server）
```

双端 runner 执行同一份 `conformance/cases/*.json`，断言行为逐项一致。

## 路线

- v0（现状）：node + py，每模型五段 CRUD rpc，JSON string 承载，status + trailer 错误映射，metadata 上下文注入
- v1：go / rust 宿主；schema 反射逐字段强类型 message；可选 server reflection（grpcurl 免 proto 文件）
