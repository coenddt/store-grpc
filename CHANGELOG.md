# Changelog — store-grpc

本文件记录 store-grpc（node / py 双实现共用 spec）的契约变更。
格式遵循 Keep a Changelog；版本号由发布流程统一 bump。

## [Unreleased]

### Fixed

- **类型化 `PermissionError` 的 trailer 机器码统一为 `ERR_PERMISSION`**：node / py 不再取宿主类名
  （`PermissionError` / `PermissionErrorMock` 等）写进 `store-error-code`，与 spec/03 及字符串前缀通道对齐；
  `details` / message 原样保留，gRPC status 仍为 `PERMISSION_DENIED`。
- **node proto 注释与实现/规范一致**：`q` 注释由「完整 GQL 查询串（自模型名始）」改为
  「模型名之后的 GQL 段（服务端前置模型名）」，与 py 实现、`spec/02` 映射表一致。
