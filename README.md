# store-grpc

Auto-generates a **standard gRPC** API per schema for the common-store data-layer family (nodejs-store / py-store / go-store) — the third skin on the same JSON schema source of truth (1st REST: [store-api](https://github.com/coenddt/store-api), 2nd GraphQL: [store-graphql](https://github.com/coenddt/store-graphql)).

gRPC here is **just another RPC skin over GQL**: proto-first, **zero protoc**. Every service / rpc / param / error maps one-to-one onto existing store schema / GQL / RBAC semantics — the adapter invents no semantics of its own.

- **Node** — `store-grpc-node` (npm): `@grpc/grpc-js` runtime + `protobufjs` **dynamic serialization**. **Zero protoc** — no `.proto` compilation step, no generated code on disk.
- **Python** — `store-grpc-py` (PyPI): `grpcio` + `grpcio-tools`, proto **compiled at runtime** from the in-memory text. **Zero manual protoc** — you never run `protoc` yourself.
- **Shared** — `spec/` (the single source of truth for proto generation / execution mapping / errors & context) + `conformance/` (cross-runtime cases; both runners assert the same JSON).

Design rule (three hard laws, `spec/00-overview.md`):

1. **Zero semantic invention** — a service / rpc / param / error / permission must map onto semantics that already exist in store. If store does not have it, this skin does not offer it.
2. **Parity first** — change `spec/` first, then both runtimes, then the conformance cases. Spec / code / cases disagreeing is a defect.
3. **No error masking** — any error store raises is mapped verbatim into a semantic gRPC error (`status` + `details` + `store-error-code` trailer). No silent success, no default fallback, no treating a neutral result as an error.

> 中文说明：[README.zh-CN.md](./README.zh-CN.md)

## Runtime matrix

| | Node | Python |
|---|---|---|
| Package | `store-grpc-node` (npm) | `store-grpc-py` (PyPI), `import store_grpc` |
| gRPC transport | `@grpc/grpc-js` — *optional* dependency | `grpcio` — *optional* extra (`[grpc]`) |
| Serialization | `protobufjs` parses the in-memory `.proto` text and encodes / decodes **dynamically** | `grpcio-tools` (`grpc_tools.protoc`) compiles the in-memory `.proto` text to a descriptor set **at runtime**, then builds message classes |
| protoc | **Zero protoc** — `protoc`/`grpcurl` are never invoked by the library | **Zero manual protoc** — `grpc_tools.protoc` runs in-process; the user never calls it |
| Generated code on disk | none | none |
| Public entry points | `buildProto`, `exportProto`, `createServer` | `build_proto`, `export_proto`, `create_server` |
| Server handle | `Promise<{ server, proto, port, shutdown() }>` | `GrpcServer`: `.server` / `.pb` / `.proto` / `.port` / `.stop(grace)` |
| Error module | `mapStoreError` (pure) | `store_grpc.errors.map_store_error` (pure) |
| Minimum runtime | Node >= 18 (`engines`) | Python >= 3.10 (`requires-python`) |
| Current version | `0.2.1` | `0.2.1` |

Both runtimes emit a **byte-identical `.proto`** for the same schema and assert the same behavior against the same shared `conformance/cases/*.json`.

## Table of contents

1. [What it is](#what-it-is)
2. [Generation model](#generation-model)
3. [When to use it](#when-to-use-it)
4. [When not to use it](#when-not-to-use-it)
5. [Installation](#installation)
6. [Quick start](#quick-start)
   - [Node](#quick-start-node)
   - [Python](#quick-start-python)
7. [Proto reference](#proto-reference)
8. [Execution mapping](#execution-mapping)
9. [Errors & context](#errors--context)
10. [Scenarios (worked examples)](#scenarios-worked-examples)
11. [Relationship to the other skins](#relationship-to-the-other-skins)
12. [v0 scope & roadmap](#v0-scope--roadmap)
13. [FAQ](#faq)
14. [Development](#development)
15. [Related projects](#related-projects)
16. [License](#license)

## What it is

`store-grpc` is a **gRPC adapter layer** on top of the common-store data layer. It is not a new data layer and does nothing beyond translating `gRPC ↔ store calls` — no auth, no rate limiting, no caching.

```
gRPC client
   │  protobuf (package store.v0)
store-grpc  (this repo: grpc-js adapter / grpcio adapter)
   │  store native API: query / queryOne / insert / update / remove + setContext
data layer (nodejs-store / py-store / go-store / rust-store)
   │  GQL → command planning
rust-store core (parse / permission / dialect)
   │
database (MongoDB / MySQL / PostgreSQL / SQLite)
```

The store instance is shared with the REST and GraphQL skins — same JSON schema, same GQL, same RBAC. The only essential difference from REST/GraphQL is that **gRPC is a static IDL protocol: the server must have a `.proto`**. This repo answers that with **proto-first / two outlets**:

- `buildProto(store, opts)` / `build_proto(store, ...)` → `.proto` text (the IDL source of truth). **Pure build, zero gRPC dependency** — always available.
- `exportProto(store, opts)` / `export_proto(store, ...)` → identical text, named to mirror store-graphql's `exportSDL` / `export_sdl`; this is the artifact you write to disk for external `protoc` / `grpcurl` / client code generation.
- `createServer(store, opts)` / `create_server(store, ...)` → the running gRPC server. It uses the **very same proto text** for runtime serialization registration (Node: `protobufjs.parse` + a hand-built grpc-js `ServiceDefinition`; Python: `grpc_tools.protoc` in-process + a generic handler). **Zero protoc step, zero generated code on disk.**

## Generation model

1. **Single source of truth** — store's pure JSON schema (`store.list()` / `store.get(name)`), exactly what the REST / GraphQL skins consume.
2. **Surface** — one service per model, five fixed rpcs:
   `List{Name}` / `Get{Name}` / `Create{Name}` / `Update{Name}` / `Delete{Name}`.
   Model annotations: `"x-grpc": {"hidden": true}` (service omitted entirely) and `"x-grpc": {"readonly": true}` (only the two query rpcs). Archived tables are filtered (`XxxDeleted` is skipped when `Xxx` is also present).
3. **JSON-string payloads (v0)** — request fields are `q` (the GQL remainder, same as REST `?q=`) + `params_json`; the response field is `data_json`. Rationale (recorded in `spec/01-proto-generation.md`): GQL query strings and params are dynamic JSON by nature, so a string carries them with **zero ambiguity and the lowest cross-runtime parity cost**. Per-field typed messages are a v1 evolution.
4. **Errors** — the REST skin's status-code mapping becomes a gRPC **status code + details + `store-error-code` trailer** (`INVALID_PARAM`, `INVALID_BODY`, `GQL_PARSE`, `CONTEXT_ERROR`, `ERR_PERMISSION`, `no_context` → `PERMISSION_DENIED`, `NOT_FOUND`, otherwise `INTERNAL`). See [Errors & context](#errors--context).
5. **Two outlets, one text** — `buildProto()` / `exportProto()` give you the `.proto`; `createServer()` registers the same text at runtime. No `.pb.go` / `_pb2.py` / generated JS is ever written by the library.
6. **Language naming convention (declared, not a parity defect)** — `.proto` field names are always `snake_case` (`params_json` / `body_json` / `set_json` / `data_json`). Each runtime presents them per its protobuf convention: Python `_pb2` uses `body_json`; Node (protobufjs) uses `bodyJson`; Go generated code uses `BodyJson`. The adapter never rewrites across those conventions.

## When to use it

- **Strongly-typed, contract-first service calls** between internal services where the `.proto` is the interface contract.
- **Polyglot clients from one artifact** — generate Go / Java / C++ / Python / Node clients from the single exported `.proto`.
- **Long-lived connections / high-throughput reads** — HTTP/2 multiplexing and binary framing instead of JSON-over-HTTP per request.
- **Dedicated ports or model subsets** — expose a curated `resources` list on its own port, separate from the REST/GraphQL surface.
- **Cross-language invariant error semantics** — every language sees the same `status` code and the same machine-readable `store-error-code`.
- **You already use nodejs-store / py-store / go-store** and want a gRPC surface without hand-writing a `.proto`.

## When not to use it

- **You want per-field typed messages / generated DTOs today** — v0 carries payloads as JSON strings; typed messages are v1.
- **You need streaming** — v0 is unary only (`requestStream: false`, `responseStream: false` for every rpc). Streaming is on the roadmap.
- **You need server reflection** (`grpcurl` without a `.proto`) — not in v0; export the `.proto` and pass it with `-proto`. Reflection is roadmapped.
- **You want a browser-native client without a proxy** — gRPC over HTTP/2 is not directly callable from browsers; gRPC-Web is out of scope.
- **You want the adapter to add auth/rate-limit/cache** — by design it does none of that; those belong in the host or a gateway.

## Installation

### Node

```bash
npm i store-grpc-node
# Serving needs @grpc/grpc-js (declared as an optional dependency; install it explicitly if your
# package manager skips optional deps):
npm i @grpc/grpc-js
# nodejs-store is an optional peer dependency — install it in the app that owns the store:
npm i nodejs-store
```

- `protobufjs` is a regular dependency (used for the `.proto` text and serialization).
- `buildProto` / `exportProto` work with **no gRPC dependency at all**; only `createServer` needs `@grpc/grpc-js`.

### Python

```bash
pip install store-grpc-py            # build/export only: zero runtime dependencies
pip install "store-grpc-py[grpc]"    # serving: grpcio + grpcio-tools
pip install py-store                 # the host that owns the store
```

- `build_proto` / `export_proto` work with **no `grpcio` installed**.
- `create_server` imports `grpcio` + `grpcio-tools` lazily; if missing it raises an install-guidance error instead of silently degrading.

## Quick start

### Quick start (Node)

```js
// server.js  — run with: node server.js
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

  // 1) Emit the .proto — feed grpcurl / protoc / client codegen.
  fs.writeFileSync('store_grpc.proto', exportProto(store));

  // 2) Serve the same schema — dynamic serialization, zero protoc.
  const { port, proto, shutdown } = await createServer(store, { port: 50051 });
  console.log(`store-grpc-node listening on 127.0.0.1:${port}`);
  // Service: store.v0.User -> ListUser / GetUser / CreateUser / UpdateUser / DeleteUser

  process.on('SIGINT', async () => { await shutdown(); process.exit(0); });
}

main();
```

Call it with `grpcurl` (the exported `.proto` is the client contract):

```bash
# Create
grpcurl -plaintext -proto store_grpc.proto \
  -d '{"body_json": "{\"name\":\"alice\",\"age\":30}"}' \
  127.0.0.1:50051 store.v0.User/CreateUser
# -> {"dataJson": "{\"_id\":\"u1\",\"name\":\"alice\",\"age\":30}"}

# List (q = GQL remainder + params_json)
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

Using the generated stub from your own Node client (grpc-js + protobufjs, no codegen):

```js
const grpc = require('@grpc/grpc-js');
const protobuf = require('protobufjs');
const { createServer } = require('store-grpc-node');

// after createServer() you already have the proto text on the handle:
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

### Quick start (Python)

```python
# server.py  — run with: python server.py
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

    # 1) Emit the .proto (same text Node emits for the same schema).
    with open('store_grpc.proto', 'w', encoding='utf-8') as f:
        f.write(export_proto(store))

    # 2) Serve it — grpcio server, proto compiled in-process at runtime (zero manual protoc).
    server = create_server(store, port=50051)
    print('store-grpc-py listening on 127.0.0.1:%d' % server.port)
    try:
        await asyncio.Event().wait()          # keep running
    finally:
        server.stop(None)

asyncio.run(main())
```

Call it with the compiled descriptor set the server exposes on the handle (`server.pb`), or with `grpcurl` using the same `store_grpc.proto`:

```bash
grpcurl -plaintext -proto store_grpc.proto \
  -d '{"q": "($condition: @c0) { name, age }", "params_json": "{\"c0\":{\"age\":30}}"}' \
  127.0.0.1:50051 store.v0.User/ListUser
```

```python
import grpc
from store_grpc import create_server

server = create_server(store, port=0)          # 0 -> random port
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

> In production, `create_server(store, port=50051)` binds and returns; call `server.stop(None)` on shutdown.

## Proto reference

The exact text `exportProto` / `export_proto` produces for a `User` model with fields `_id`, `name`, `age` (this is a real generated artifact, see `node/test/tmp-proto-check/store_grpc.proto`):

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

Points to note:

- **Package** is fixed to `store.v0` (`PACKAGE` in both runtimes). Every message/service lives under it.
- **Service name** = model name with the first letter upper-cased, the rest verbatim (`user` → `User`, `user_profile` → `User_profile`).
- **rpc name** = verb prefix (`List` / `Get` / `Create` / `Update` / `Delete`) + service name.
- **`opts.resources`** (`createServer`) / `resources=` (`create_server`) restrict generation to an explicit list; otherwise `store.list()` is used with archived tables filtered.
- **`opts.idField`** / `id_field` sets the key used by `Get` / `Update` / `Delete`; it defaults to `_id` on both sides.
- **description pass-through**: the model-level `description` is emitted as the service `//` comment (see `// 用户表：平台账号主档` above); rpc comments are fixed text. The adapter passes descriptions through without rewriting them.
- **`q` semantics**: the operative contract (`spec/01`, `spec/02`, and both runtimes) is that `q` is the GQL fragment **after** the model name — the adapter evaluates `store.query(name + q, params)` (`node/src/index.js`, `py/src/store_grpc/server.py`), and the conformance case `users-crud.json` passes `"q": "($condition: @c0) { name, age }"`. The inline comment shown in the generated `.proto` describes the concatenated form loosely; the remainder form is authoritative.
- **Language conventions**: `.proto` fields stay `snake_case`; Python `_pb2` exposes `body_json`, Node/`protobufjs` exposes `bodyJson`, Go codegen exposes `BodyJson` (`spec/01`).
- **`x-grpc` annotations are schema-level, not emitted into the `.proto`**:

  | Annotation | Effect |
  |---|---|
  | `"x-grpc": {"hidden": true}` | the whole service is **not generated** |
  | `"x-grpc": {"readonly": true}` | only `List` / `Get` rpcs are generated |

- **Archived-table filtering**: a model `XxxDeleted` is skipped when `Xxx` is also present in the model list (byte-identical to store-api / store-graphql).
- **Name-collision guard**: two model names that collide after the first-letter upper-casing (`user` + `User`) raise a build-time error prefixed `ERR_NAME_CONFLICT:` — never silently merged.

## Execution mapping

Every rpc handler follows the same skeleton: parse the request JSON → call the store's native API → `JSON.stringify` the success value into `StoreReply.data_json`; any error is mapped by the `spec/03` chain and the call is terminated.

For model `User`, service `User`, `id_field = _id`, and schema projection `proj` (` { name, age }` shape):

| rpc | Request | Store call | Success semantics |
|---|---|---|---|
| `ListUser` | `q`, `params_json` | `store.query(name + (q or proj), JSON.parse(params_json))` | array JSON |
| `GetUser` | `id` | `store.queryOne('User($condition: @c0)' + proj, { c0: { _id: id } })` | object JSON; `null` ⇒ `NOT_FOUND` |
| `CreateUser` | `body_json` | `store.insert('User', JSON.parse(body_json))` | inserted document JSON |
| `UpdateUser` | `id`, `set_json` | `store.update('User', { _id: id }, JSON.parse(set_json))` | `store.update` return value as-is (aligned with REST `PATCH`, no re-read) |
| `DeleteUser` | `id` | `store.remove('User', { _id: id })` | `store.remove` return value as-is |

**Request parsing rules** (`spec/02-execution-mapping.md`):

| Field | Rule | On violation |
|---|---|---|
| `params_json` | empty string ⇒ params is `undefined` (not passed); otherwise must be valid JSON | ⇒ `INVALID_ARGUMENT` + `INVALID_PARAM` |
| `body_json` | must be a JSON **object** (arrays / scalars / invalid JSON rejected) | ⇒ `INVALID_ARGUMENT` + `INVALID_BODY` |
| `set_json` | same as `body_json` | ⇒ `INVALID_ARGUMENT` + `INVALID_BODY` |
| `q` | empty ⇒ server builds `name + proj`; non-empty ⇒ server builds `name + q` (the GQL remainder after the model name, **fully source-identical to REST `?q=`**) | core raises `ERR_GQL_PARSE:` ⇒ `spec/03` chain |
| `id` | passed through verbatim as the key value; no type rewriting | — |

**Schema projection (`proj`)**: built from store metadata as an explicit projection string ` { f1, f2 }` (fields **and** computes count as fields, aligned with store-graphql). Read order: `store.get(name)` (nodejs form) → py-store's `schema` module when the `Store` class does not expose `get`. If neither is available or there are no fields, the projection is the empty string (data carries only `_id` — the explicit consequence of an incomplete upstream schema, same as REST). Only `ListUser` uses `proj`, and only when `q` is absent; `GetUser` always uses `proj`.

**`limit` guard: none at this skin.** GraphQL's default-50 / cap-1000 belongs to that skin's spec; REST and gRPC pass `q` through without a row guard (core's cap applies in the text2query tier). This skin aligns with REST.

**Context injection (`contextProvider`)** (`spec/02`):

- `opts.contextProvider` (Node) / `context_provider=` (Python) is a per-call hook that receives this call's metadata (Node: grpc-js `call.metadata`; Python: `ServicerContext.invocation_metadata()`).
- Its return value is injected via `store.setContext(ctx)`. Returning `null`/`undefined` still injects **explicitly** (`setContext(null)`) — the clear semantics must land, so a stateful runtime never leaks the previous request's identity across requests.
- A hook that throws goes through the `spec/03` chain: `PermissionError` ⇒ `PERMISSION_DENIED`; anything else ⇒ `UNAUTHENTICATED` with the original message passed through.

**Server API**:

- Node: `createServer(store, opts) -> Promise<{ server, proto, port, shutdown() }>`. With `opts.port` set, it resolves after `bindAsync` completes (`port: 0` ⇒ random port); without `port`, it only registers.
- Python: `create_server(store, *, port=None, host='127.0.0.1', resources=None, id_field='_id', context_provider=None, permission_error=None) -> GrpcServer` (synchronous; `.port` / `.stop(grace)`, same shape as Node).
- Transport deps are optional on both sides: if `@grpc/grpc-js` (Node) or `grpcio` + `grpcio-tools` (Python) are missing, `createServer` / `create_server` raise an error **with install guidance**; `buildProto` / `exportProto` / `build_proto` / `export_proto` always work.
- Python carries a sync-`grpcio` vs async-`py-store` compatibility: py-store instance methods are `async`, and the adapter detects awaitables — they run on a private event loop inside the worker thread, while sync return values are used directly. Both host shapes behave identically; the adapter rewrites no semantics.

## Errors & context

Where REST carries errors as HTTP status + JSON body, gRPC carries a three-part error (`spec/03-errors-context.md`):

1. **status code** (`grpc.StatusCode`)
2. **details** — the original message, passed through; empty string if unavailable (**never fabricated**)
3. **trailer metadata** `store-error-code` — a stable machine code (same code table as REST's `error.code`)

**Status mapping table:**

| `store-error-code` | gRPC status | REST | Trigger |
|---|---|---|---|
| `INVALID_PARAM` | `INVALID_ARGUMENT` | 400 | `params_json` is not valid JSON |
| `INVALID_BODY` | `INVALID_ARGUMENT` | 400 | `body_json` / `set_json` is not an object or not valid JSON |
| `GQL_PARSE` | `INVALID_ARGUMENT` | 400 | core raises `ERR_GQL_PARSE:` (details strips the prefix) |
| `CONTEXT_ERROR` | `UNAUTHENTICATED` | 401 | `contextProvider` throws a non-permission error |
| `ERR_PERMISSION` | `PERMISSION_DENIED` | 403 | `PermissionError` class / `ERR_PERMISSION:` prefix |
| `no_context` | `PERMISSION_DENIED` | 403 | host `NoContextError` (machine code `no_context`) / core `ERR_NO_CONTEXT:` prefix (requireContext on and ctx missing — same permission tier) |
| `NOT_FOUND` | `NOT_FOUND` | 404 | `queryOne` empty result |
| `err.code` / class name | `INTERNAL` | 500 | everything else (code = `err.code`, else class/type name, else `STORE_ERROR`) |

**Decision order (identical on both runtimes; changing it requires a spec change first):**

```
adapter guards (INVALID_PARAM / INVALID_BODY / NOT_FOUND / CONTEXT_ERROR)
  -> PermissionError class check (by type when store.PermissionError is available; never by message text)
  -> ERR_PERMISSION: prefix check (core's stable prefix contract)
  -> NoContext (machine code `no_context` / ERR_NO_CONTEXT: prefix; same permission tier) -> PERMISSION_DENIED
  -> ERR_GQL_PARSE: prefix check (details strips the prefix)
  -> everything else -> INTERNAL, passed through
```

**No error masking:**

- The message store raises is passed through **verbatim** into details; if unavailable it becomes an empty string — never a fabricated message.
- No catching-and-rewriting into success, no default fallback, no treating a neutral result as an error.
- A successful `StoreReply` never contains an error field (errors travel only over the gRPC status channel).

**Context (metadata semantics):**

- Request metadata keys are not case-rewritten (gRPC lower-cases metadata itself; the adapter hands it to the provider as-is).
- A permission rejection is decided **before** the handler's semantics run — when `contextProvider` throws, the store is never called (the smoke tests assert `store.gqlLog.length` is unchanged).

## Scenarios (worked examples)

Each scenario follows: **business background → call → response → why this wins.**

### Scenario 1 — Service-to-service CRUD between microservices

**Background.** The `billing` service must read and write `User` records owned by the `accounts` service. Both are internal; neither is a browser. Instead of shipping a bespoke REST client, `accounts` exports the `.proto` from the same store it already registers and `billing` calls the generated service.

**Call.**

```bash
grpcurl -plaintext -proto store_grpc.proto \
  -d '{"body_json": "{\"name\":\"bob\",\"age\":41}"}' \
  accounts.internal:50051 store.v0.User/CreateUser

grpcurl -plaintext -proto store_grpc.proto \
  -d '{"id": "u2"}' \
  accounts.internal:50051 store.v0.User/GetUser
```

**Response.**

```json
{"dataJson": "{\"_id\":\"u2\",\"name\":\"bob\",\"age\":41}"}
```

**Why this wins.** The contract is the `.proto`, not prose in a wiki — `billing` cannot send a field that does not exist, and the payload is binary on the wire. The server side is the *same* store instance the accounts REST/GraphQL surfaces use, so there is no second data path to keep consistent.

### Scenario 2 — One `.proto`, many language clients

**Background.** A Go data pipeline, a Java batch job, and a Node dashboard all need to query `User`. Rather than three hand-written HTTP clients, the team generates clients from one artifact.

**Call (produce the artifact, then codegen).**

```bash
# Node: write the .proto from the store
node -e "const {exportProto}=require('store-grpc-node');const fs=require('fs');\
fs.writeFileSync('store_grpc.proto', exportProto(require('nodejs-store').store))"

# Now feed it to any protoc plugin / client generator (Go, Java, Python, C++, ...):
protoc -I. --go_out=. --go-grpc_out=. store_grpc.proto
```

**Response.** Each generated client calls the same rpcs:

```
store.v0.User/ListUser   store.v0.User/GetUser    store.v0.User/CreateUser
store.v0.User/UpdateUser store.v0.User/DeleteUser
```

**Why this wins.** The `.proto` is the single interface artifact; `exportProto` / `export_proto` is the only thing you ever run, and it is the *same text* the server uses for serialization — clients and server can never drift on the message shape.

### Scenario 3 — Long-lived connection, high-throughput reads

**Background.** A read-heavy dashboard polls `User` thousands of times per minute. Creating an HTTP connection per poll wastes handshakes.

**Call.**

```js
// Reuse ONE grpc-js channel for the process lifetime.
const client = new Client(`127.0.0.1:${port}`, grpc.credentials.createInsecure());
setInterval(() => {
  client.ListUser({ q: ' { name, age }' }, (err, res) => { /* ... */ });
}, 50);
```

**Response.** `{"dataJson":"[...]"}` — a single multiplexed HTTP/2 stream carries every call.

**Why this wins.** gRPC keeps the channel (and HTTP/2 connection) open across calls, multiplexing concurrent requests over one connection with binary framing — far less per-call overhead than re-establishing HTTP and re-parsing JSON bodies on each poll.

### Scenario 4 — Dedicated port / curated model subset

**Background.** An internal admin tool needs `User` and `AuditEvent`, but the public REST surface also exposes `Order`, `Payment`, and `Cart`. The team does not want the admin tool's gRPC endpoint to see the payment models at all.

**Call.**

```js
const { createServer } = require('store-grpc-node');
// Only these two services exist on this port — generated from the same store.
await createServer(store, { resources: ['User', 'AuditEvent'], port: 50052 });
```

```bash
grpcurl -plaintext -proto admin.proto -d '{}' 127.0.0.1:50052 store.v0.User/ListUser   # OK
grpcurl -plaintext -proto admin.proto -d '{}' 127.0.0.1:50052 store.v0.Payment/ListPayment
# ERROR: Code: Unimplemented ...
```

**Response.** `ListUser` succeeds; `ListPayment` returns `UNIMPLEMENTED` because the service was never registered on this port.

**Why this wins.** `resources` curates the generated surface per server, so a separate port (or a separate process) can expose exactly the models a consumer is allowed to see — without a second schema, and without leaking unrelated model names in the IDL.

### Scenario 5 — Coexisting with REST and GraphQL

**Background.** A platform exposes REST for public clients, GraphQL for the web app, and gRPC for internal services — all over one store and one schema.

**Call.**

```js
// store-gateway composes the three skins declaratively (all optional peer deps, per-skin `enabled`):
const { createGateway } = require('store-gateway');
await createGateway(store, {
  rest:     { enabled: true, port: 8080 },
  graphql:  { enabled: true, port: 4000 },
  grpc:     { enabled: true, port: 50051 },
});
```

**Response.** The same `User` model is reachable as `GET /users?q=...` (REST), a GraphQL field (GraphQL), and `store.v0.User/ListUser` (gRPC) — all returning the same records.

**Why this wins.** The three skins are independent packages with zero cross-dependencies; the gateway only wires them. You can start with REST and add gRPC later without touching the schema, or run any skin standalone (the gateway is optional).

### Scenario 6 — Error semantics that stay identical across languages

**Background.** A Go caller, a Python caller, and a Node caller all call `GetUser` for a deleted record. Each must distinguish "missing" from "forbidden" from "server bug" programmatically — without string-matching a message.

**Call.**

```bash
grpcurl -v -plaintext -proto store_grpc.proto \
  -d '{"id": "u1"}' 127.0.0.1:50051 store.v0.User/GetUser
```

**Response.**

```
ERROR:
  Code: NotFound
  Message: 记录不存在: _id=u1
Trailers:
  store-error-code: NOT_FOUND
```

**Why this wins.** The `status` code is the coarse class; the `store-error-code` trailer is the stable machine code; `details` carries the original message verbatim. All three languages see the identical `status` + trailer, so callers branch on codes, not on localized text — and the adapter never hides a store error behind a generic "internal" (see the [Errors & context](#errors--context) table).

### Scenario 7 — Hide internal models entirely

**Background.** A model `SecretLog` holds credentials material that must never be reachable over gRPC (it still exists in store for REST with different guards).

**Call.**

```js
store.register({
  name: 'SecretLog',
  'x-grpc': { hidden: true },   // schema-level annotation
  fields: { /* ... */ },
});
```

```bash
grpcurl -plaintext -proto store_grpc.proto -d '{}' 127.0.0.1:50051 store.v0.SecretLog/ListSecretLog
# ERROR: Code: Unimplemented ...  (service was never generated)
```

**Response.** No `service SecretLog` appears in the exported `.proto`; the rpc is `UNIMPLEMENTED`.

**Why this wins.** Exposure is controlled at the schema, alongside the model definition, and is consistent across every language and every generated artifact — the hidden model never appears in the IDL at all.

### Scenario 8 — Read-only model

**Background.** `AuditEvent` is written only by an internal pipeline; readers must be able to query it over gRPC but never create, update, or delete through this surface.

**Call.**

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

**Response.** The generated service contains only `ListAuditEvent` and `GetAuditEvent`.

**Why this wins.** Read-only access is declared once at the schema and enforced at the protocol surface — there is no `Create`/`Update`/`Delete` rpc to accidentally call, so a read-only consumer cannot mutate the table through gRPC.

## Relationship to the other skins

`store-grpc` is one of four protocol skins over the same store, plus a composer:

| Skin | Shape | Package |
|---|---|---|
| [store-api](https://github.com/coenddt/store-api) | REST (HTTP status + JSON body) | REST |
| [store-graphql](https://github.com/coenddt/store-graphql) | GraphQL SDL | GraphQL |
| **store-grpc** (this repo) | gRPC service per model | gRPC |
| store-mcp | MCP tool surface | MCP |
| [store-gateway](https://github.com/coenddt/store-gateway) | declarative composer | — |

- The skins are **architecturally independent**: separate repos, separate packages (npm / PyPI), zero cross-dependencies. The only shared things are the store instance (one JSON schema source of truth) and each skin's own `spec/` + `conformance/`.
- The skins are **pluggably composable** via `store-gateway` (optional dependencies on every skin, per-skin `enabled` toggles). Using a single skin needs no gateway at all.
- Mapping lineage: gRPC `q` is source-identical to REST `?q=`; service/rpc naming mirrors GraphQL's model-per-type view; the `x-grpc` annotations align with `x-graphql`; the error code table is the same as REST's.

## v0 scope & roadmap

**v0 (current, `0.2.1`):**

- Runtimes: Node (`store-grpc-node`) + Python (`store-grpc-py`).
- Five CRUD rpcs per model (`List` / `Get` / `Create` / `Update` / `Delete`), unary only.
- JSON-string payloads (`q` + `params_json` in, `data_json` out).
- proto-first: `buildProto` / `exportProto` (+ Python twins) with runtime serialization from the same text; zero generated code on disk.
- Error mapping: gRPC status + details + `store-error-code` trailer.
- Context via metadata (`contextProvider` / `context_provider`), with explicit `setContext(null)` clearing.
- Schema-level `x-grpc` annotations: `hidden`, `readonly`; archived-table filtering; PascalCase collision guard.

**Roadmap:**

- **v1** — `go` / `rust` hosts; per-field typed messages from schema reflection; optional server reflection (so `grpcurl` needs no `.proto` file).
- Streaming rpcs (`requestStream` / `responseStream`) are not in v0 — every rpc is unary.

**Spec boundary:** behavior is defined by `spec/00-overview.md`, `spec/01-proto-generation.md`, `spec/02-execution-mapping.md`, `spec/03-errors-context.md`. Spec, code, and conformance cases must agree; a disagreement is a defect.

## FAQ

1. **Does a client need the `.proto` file?** Yes. gRPC is a static IDL protocol; the server must publish the contract. Obtain it via `exportProto` / `export_proto`. v0 has no server reflection, so `grpcurl` must be given `-proto store_grpc.proto`.
2. **Why JSON strings instead of typed messages?** A v0 decision recorded in `spec/01`: GQL queries and params are dynamic JSON by nature, so a string carries them with zero ambiguity and the lowest cross-runtime parity cost. Typed messages are a v1 item and require a spec change first.
3. **Is there a row-count guard (default/cap like GraphQL's 50/1000)?** No. GraphQL's guard belongs to that skin's spec; REST and gRPC pass `q` through without a row guard (core caps in the text2query tier). This skin aligns with REST.
4. **How do I get the created record's id?** From the `Create*` response's `data_json` (the inserted document, including `_id`).
5. **What does `Get` return when nothing matches?** `NOT_FOUND`: status `NOT_FOUND`, `store-error-code: NOT_FOUND`, details like `记录不存在: _id=u1`. It never returns an empty success.
6. **Does `Update` re-read the record?** No. It returns `store.update`'s raw return value, aligned with REST `PATCH` (`spec/02`) — there is no second read.
7. **How is identity / auth passed?** Through request metadata, transformed by your `contextProvider` / `context_provider` into the context injected via `store.setContext`. Returning `null` explicitly clears the context. A permission error becomes `PERMISSION_DENIED`; any other hook error becomes `UNAUTHENTICATED`.
8. **Why is my field `data_json` in Python but `dataJson` in Node?** `.proto` fields are `snake_case`; protobufjs exposes camelCase JS properties, `_pb2` keeps snake_case, Go codegen uses PascalCase. This is a declared language convention (`spec/01`), not a parity defect — access fields per your target language's convention.
9. **Can I serve only some models, or on a separate port?** Yes. Pass `resources: ['User', 'Order']` (`resources=[...]` in Python) and/or a distinct `port`; models outside the list are not generated, and their rpcs return `UNIMPLEMENTED`.
10. **What if two model names collide after upper-casing the first letter?** Generation raises a build-time error prefixed `ERR_NAME_CONFLICT:`; they are never silently merged.
11. **Is streaming supported?** Not in v0 — every rpc is unary (`requestStream: false`, `responseStream: false`). Streaming is roadmapped.
12. **Do I need `protoc` installed?** No. Node uses `protobufjs` (no `protoc`); Python uses `grpcio-tools`, which runs `protoc` in-process at runtime — you never invoke it. `buildProto` / `exportProto` (and Python twins) work with no gRPC library installed at all.

## Development

```bash
# Node (mock store + real @grpc/grpc-js server) — runs the smoke, error-matrix and conformance suites
cd node && npm i && npm test

# Python (mock store + real grpcio server)
cd py && pip install -e ".[dev]" && pytest
```

- Node tests: `node --test` over `node/test/*.test.js` (`smoke`, `error-matrix`, `conformance`).
- Python tests: `pytest` over `py/tests/` (`test_smoke`, `test_error_matrix`, `test_conformance`).
- Both runners execute the shared `conformance/cases/*.json` (the `users-crud` case has 10 steps) and assert identical behavior.
- The `proto round-trip` test writes the exported text to disk and re-parses / re-compiles it, proving the export is a legal `.proto`.

Contributing rule: **change `spec/` first, then both runtimes, then the conformance cases.** Spec / code / cases disagreeing is a defect.

## Related projects

- [store-api](https://github.com/coenddt/store-api) — REST skin.
- [store-graphql](https://github.com/coenddt/store-graphql) — GraphQL skin.
- [store-gateway](https://github.com/coenddt/store-gateway) — declarative composer for the skins.
- store-mcp — MCP tool skin.
- nodejs-store / py-store / go-store / rust-store — the data-layer family (hosts + the Rust core).
- meta-store, create-store-app, store-rbac-bootstrap, store-resource-bootstrap — control plane, scaffold and bootstrap plugins.

## License

MIT — see [LICENSE](./LICENSE).
