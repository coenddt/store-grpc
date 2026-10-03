# 03 — 错误与上下文：gRPC 映射

## HTTP 皮肤 → gRPC 的错误承载差异

REST 的错误是 HTTP 状态码 + JSON body；gRPC 的错误承载为三件套：

1. **status code**（`grpc.StatusCode`）
2. **details**：原 message（透传，取不到置空串——禁伪造）
3. **trailer metadata** `store-error-code`：稳定机器码（与 REST 错误 body 的 `error.code` 同码表）

## 状态码映射表

| 码（store-error-code） | gRPC status | REST 对应 | 触发 |
|---|---|---|---|
| `INVALID_PARAM` | `INVALID_ARGUMENT` | 400 | params_json 非法 JSON |
| `INVALID_BODY` | `INVALID_ARGUMENT` | 400 | body_json / set_json 非对象或非法 JSON |
| `GQL_PARSE` | `INVALID_ARGUMENT` | 400 | core 抛 `ERR_GQL_PARSE:`（details 剥前缀取原文） |
| `CONTEXT_ERROR` | `UNAUTHENTICATED` | 401 | contextProvider 非权限类抛错 |
| `ERR_PERMISSION` | `PERMISSION_DENIED` | 403 | PermissionError 类 / `ERR_PERMISSION:` 前缀 |
| `NOT_FOUND` | `NOT_FOUND` | 404 | queryOne 空结果 |
| err.code / 类名 | `INTERNAL` | 500 | 其余一切错误（码取 err.code，缺省类名，再缺省 `STORE_ERROR`） |

## 判定顺序（双端一致，改动必须先改本 spec）

```
适配层守卫（INVALID_PARAM / INVALID_BODY / NOT_FOUND / CONTEXT_ERROR）
  → PermissionError 类判定（store.PermissionError 可得时按类型，禁按文案匹配）
  → ERR_PERMISSION: 前缀判定（core 稳定前缀契约）
  → ERR_GQL_PARSE: 前缀判定（details 剥前缀取原文）
  → 其余 INTERNAL 透传
```

## 禁掩盖错误

- store 抛出的错误 message **原样透传**进 details；取不到置空串，禁伪造文案。
- 禁 catch 后洗成成功、禁回退默认值、禁把中性结果当错误。
- 成功的 `StoreReply` 中不得出现错误字段（错误只经 gRPC status 通道）。

## 上下文（metadata 语义）

- 请求元数据键名不做大小写改写（gRPC metadata 本身小写化，适配层原样交给 provider）。
- 权限拒绝必须在**进 handler 语义之前**判定（contextProvider 抛错时 store 调用不发生）。
