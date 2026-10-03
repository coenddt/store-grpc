# 00 — 总览：定位与分层

## 定位

store-grpc 是 common-store 数据层家族之上的 **gRPC 适配层**，是同一份数据 schema 的第三层皮肤（第一层 REST：store-api；第二层 GraphQL：store-graphql）。它不是新的数据层，也不做认证、限流、缓存等任何超出「gRPC ↔ store 调用翻译」的事。

## 分层

```
gRPC 客户端
   │  protobuf (store.v0)
store-grpc（本仓库：grpc-js 适配器 / grpcio 适配器）
   │  store 原生 API：query / queryOne / insert / update / remove + setContext
数据层（nodejs-store / py-store / go-store / rust-store）
   │  GQL → 命令规划
rust-store core（解析 / 权限 / 方言）
   │
数据库（MongoDB / MySQL / PostgreSQL / SQLite）
```

## 与 REST / GraphQL 皮肤的关系

- 三层皮肤**架构独立**：各自独立仓库、独立包（npm / PyPI），代码零互相依赖；共享的只有 store 实例（同一份 JSON schema 事实源）与各自的 spec / conformance。
- 三层皮肤**可配置插拔**：由姊妹仓库 store-gateway 提供组合器（对三层皮肤全部 optional 依赖，配置开关启用/停用）；单用某层皮肤时不需要 gateway。
- gRPC 与 REST/GraphQL 的唯一本质差异：gRPC 是静态 IDL 协议，server 端必须有 `.proto`。本仓库的应对是 **proto-first 双出口**——`buildProto()` 产出 `.proto` 字符串（IDL 事实源），运行时用同一份字符串做动态序列化注册（node：protobufjs parse + grpc-js 手工 ServiceDefinition；py：grpc_tools 运行时编译 + generic handler），零 protoc 步骤、零生成代码落盘。

## 三条铁律

1. **零语义发明**：service、rpc、参数、错误、权限语义必须能一一对应到 store 已有语义；store 没有的语义本层不提供。无法对应的设计不允许进 spec。
2. **双端 parity 先行**：任何变更先改 spec，再双端同步实现，再补 conformance 用例。规范、代码、用例三者不一致即为缺陷。
3. **禁掩盖错误**（遵循项目 `no-error-masking` 准绳）：store 抛出的任何错误必须原样映射为带语义的 gRPC 错误（status code + details + trailer 错误码），禁止静默吞掉、禁止回退默认值、禁止把中性结果当错误。成功响应的 `StoreReply` 中不得出现错误字段。
