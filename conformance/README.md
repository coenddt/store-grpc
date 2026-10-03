# conformance — 双端一致性用例

同一份 JSON（`cases/*.json`），node / py 各自执行，断言 gRPC 行为逐项一致：

- `request`：通用形状（`body` / `set` / `params` / `q` / `id`），runner 负责编码为各自端的 message 字段（`body_json` / `set_json` / `params_json`）。
- `expect.ok`：成功 ⇒ status OK，`data_json` 解析后断言 `dataKeys` / `listLength`。
- `expect.statusCode` + `errorCode`：错误 ⇒ status code + trailer `store-error-code` 断言（spec/03）。

语义变更必须先改 `../spec/`，再双端同步实现，最后补 / 改本目录用例。规范、代码、用例三者不一致即为缺陷。
