# Changelog — store-grpc

本文件记录 store-grpc（node / py 双实现共用 spec）的契约变更。
格式遵循 Keep a Changelog；版本号由发布流程统一 bump。

## [Unreleased]

### Fixed

- **类型化 `PermissionError`：旧 trailer 值保留 + 新增规范化 trailer**：`store-error-code` 继续取
  `err.code` 或宿主类名（不惊动按旧值匹配的调用方）；新增 `store-error-code-normalized`，权限类统一
  `ERR_PERMISSION`，其余码与 `store-error-code` 相同。`details` / message 原样保留，
  gRPC status 仍为 `PERMISSION_DENIED`。node / py 同步。
- **node proto 注释与实现/规范一致**：`q` 注释由「完整 GQL 查询串（自模型名始）」改为
  「模型名之后的 GQL 段（服务端前置模型名）」，与 py 实现、`spec/02` 映射表一致。
