# Changelog — store-grpc

本文件记录 store-grpc（node / py 双实现共用 spec）的契约变更。
格式遵循 Keep a Changelog；版本号由发布流程统一 bump。

## [Unreleased]

> 目标版本 **1.0.0**（未发布；版本号由发布流程统一 bump）。破坏性变更只有一条（错误落点收敛），
> 其余均为「旧值保留 + 新增规范化字段」的兼容改动。

### Breaking

#### `ERR_RESOURCE_NOT_FOUND:` 落点：`INTERNAL` → `NOT_FOUND`

- **改了什么**：core 以 `ERR_RESOURCE_NOT_FOUND:` 前缀报「目标资源不存在」时，此前没有任何分支命中，
  直接落 `INTERNAL`（trailer 取异常类名 `Exception`）。现新增独立分支（先于 `ERR_NOT_FOUND:` 匹配），
  映射为 gRPC status `NOT_FOUND`，`details` 剥掉 `ERR_RESOURCE_NOT_FOUND:` 前缀，`store-error-code: NOT_FOUND`。
- **下游要动哪里**：把 `INTERNAL` 当作「服务端故障 / 可重试」的客户端需改为按 `NOT_FOUND` 处理
  （不重试、按「目标不存在」走业务分支）；告警规则中「INTERNAL 即后端异常」的判定需排除这一类。

### Added

- **`store-error-code-normalized` trailer（非破坏）**：承载规范化机器码。权限类统一 `ERR_PERMISSION`；
  其余码与 `store-error-code` 相同。新增 trailer 不改变既有 `store-error-code` 的取值，
  按旧 trailer 精确匹配的客户端不受影响；新客户端可只读规范化值。node / py 同步。

### Fixed

- **类型化 `PermissionError`：旧 trailer 值保留**：`store-error-code` 继续取 `err.code`（或宿主异常类名，
  如 `PermissionError` / `PermissionErrorMock`），规范化值放 `store-error-code-normalized`。`details` /
  message 原样保留，gRPC status 仍为 `PERMISSION_DENIED`。
- **node proto 注释与实现/规范一致**：`q` 注释由「完整 GQL 查询串（自模型名始）」改为
  「模型名之后的 GQL 段（服务端前置模型名）」，与 py 实现、`spec/02` 映射表一致。
