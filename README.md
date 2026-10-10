# store-grpc

Auto-generates a **standard gRPC** API per schema for the common-store data-layer family (nodejs-store / py-store / go-store) — the third skin on the same JSON schema source of truth (1st REST: [store-api](https://github.com/coenddt/store-api), 2nd GraphQL: [store-graphql](https://github.com/coenddt/store-graphql)).

- Node: `store-grpc-node` (npm) — grpc-js runtime, protobufjs dynamic serialization, **zero protoc**
- Python: `store-grpc-py` (PyPI) — grpcio + grpcio-tools runtime compilation, **zero manual protoc**
- Shared: `spec/` (the single source of truth for proto generation / execution mapping / errors & context) + `conformance/` (cross-runtime cases, both runners assert the same JSON)

Design rule: **gRPC is just another RPC skin over GQL** — zero semantic invention; every service/rpc/param/error maps onto existing store schema / GQL / RBAC semantics. Proto-first with two outlets: `buildProto()` / `exportProto()` emits the `.proto` (IDL source of truth), and the same text drives runtime serialization — no generated code on disk.

> 中文说明：[README.zh-CN.md](./README.zh-CN.md)

## Quick start

### Node (`store-grpc-node`)

```js
const { init, store } = require('nodejs-store');
const { exportProto, createServer } = require('store-grpc-node');

await init({ default: 'mongodb://...' });
store.register(defn);                     // pure JSON schema — register and go

const proto = exportProto(store);         // .proto text: feed protoc / grpcurl / client codegen
const { port, shutdown } = await createServer(store, { port: 50051 });
// client: stub for store.v0.User { ListUser / GetUser / CreateUser / UpdateUser / DeleteUser }
```

### Python (`store-grpc-py`)

```python
from py_store import init, store
from store_grpc import export_proto, create_server

await init({'default': 'mongodb://...'})
store.register(defn)

proto = export_proto(store)               # .proto text
server = create_server(store, port=50051) # grpcio server, runtime-compiled
# grpcurl -plaintext -d '{"q": "($condition: @c0)", "params_json": "{...}"}' localhost:50051 store.v0.User/ListUser
```

## Generation model

1. **Single source**: store's pure JSON schema (same as REST / GraphQL skins)
2. **Surface**: one service per model — `List{Name}` / `Get{Name}` / `Create{Name}` / `Update{Name}` / `Delete{Name}`; annotations `"x-grpc": {"hidden": true | "readonly": true}`; archived tables filtered (`XxxDeleted` with `Xxx` present)
3. **JSON-string payloads (v0)**: `q` (GQL remainder after the model name, same as REST `?q=`) + `params_json` in, `data_json` out — zero ambiguity, lowest cross-runtime parity cost
4. **Errors**: HTTP-skin status mapping → gRPC status + details + `store-error-code` trailer (`INVALID_PARAM`/`INVALID_BODY`/`GQL_PARSE`/`CONTEXT_ERROR`/`ERR_PERMISSION`/`no_context`→`PERMISSION_DENIED`/`NOT_FOUND`/…), no error masking

## Pluggable with the other skins

All three skins are independent packages with zero cross-dependencies; combine them declaratively with [store-gateway](https://github.com/coenddt/store-gateway) (optional peer deps, per-skin `enabled` switches), or use any skin alone.

## Development

```bash
node:    cd node && npm i && npm test        # 10 cases (mock store + real grpc-js server)
python:  cd py && pip install -e ".[dev]" && pytest   # 10 cases (mock store + real grpcio server)
```

Both runners execute the shared `conformance/cases/*.json` and assert identical behavior.

## Roadmap

- v0 (current): node + py, five-rpc CRUD per model, JSON-string payloads, status/trailer error mapping, context via metadata
- v1: go / rust hosts; per-field typed messages from schema reflection; optional server reflection for grpcurl
