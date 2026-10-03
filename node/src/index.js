'use strict';

/**
 * store-grpc-node — 为 nodejs-store 已注册 schema 自动生成 gRPC service。
 *
 * 语义依据：../spec/*.md（双端 parity，改动先改 spec）。
 * 设计哲学：gRPC 只是 GQL 的又一层 RPC 皮肤 —— 适配层零语义发明。
 * proto-first 双出口：
 *   buildProto(store, opts)  → .proto 字符串（IDL 事实源，纯构建，零 gRPC 依赖）
 *   exportProto(store, opts) → 同上（命名对齐 store-graphql 的 exportSDL，喂外部 protoc / codegen）
 *   createServer(store, opts) → grpc-js server（同一份 proto 字符串做运行时序列化注册，零 protoc）
 *
 * store 端口契约见 spec/00：list/get/query/queryOne/insert/update/remove/setContext。
 */

const ARCHIVE_SUFFIX = 'Deleted';
const PACKAGE = 'store.v0';

// ── spec/01：归档表过滤（与 store-api / store-graphql 逐字一致）──
function filterArchived(names) {
  const set = new Set(names);
  return names.filter((n) => !(n.endsWith(ARCHIVE_SUFFIX) && set.has(n.slice(0, -ARCHIVE_SUFFIX.length))));
}

// ── spec/01：service 名 = 模型名首字母大写、其余原样 ──
function serviceName(name) {
  return name.charAt(0).toUpperCase() + name.slice(1);
}

// ── spec/02：schema 投影（fields + computes 均视为字段，与 store-graphql 对齐）──
function schemaProjection(store, name) {
  const meta = typeof store.get === 'function' ? store.get(name) : null;
  const fields = meta
    ? { ...(meta.fields || {}), ...(meta.computes || {}) }
    : null;
  const keys = fields ? Object.keys(fields) : [];
  return keys.length ? ` { ${keys.join(', ')} }` : '';
}

// ── spec/01：proto 文本生成 ──

const COMMON_MESSAGES = `message ListRequest {
  string q = 1;           // 完整 GQL 查询串（自模型名始）；空串 ⇒ 服务端补 schema 投影
  string params_json = 2; // GQL params（如 {"c0":{"age":{"$gte":18}}}）的 JSON 编码
}

message GetRequest     { string id = 1; }

message CreateRequest  { string body_json = 1; }

message UpdateRequest  { string id = 1; string set_json = 2; }

message DeleteRequest  { string id = 1; }

message StoreReply     { string data_json = 1; }
`;

const RPC_COMMENTS = {
  list: '列表查询：q 为完整 GQL 查询串（自模型名始，与 REST ?q= 同源）；空串 ⇒ 服务端按 schema 投影',
  get: '单条查询：按 id_field 精确匹配；无记录 ⇒ NOT_FOUND',
  create: '插入：body_json 必须为 JSON 对象编码',
  update: '按 id_field 定位后部分更新：set_json 必须为 JSON 对象编码；返回 store.update 原始结果',
  remove: '按 id_field 定位删除：返回 store.remove 原始结果',
};

/**
 * 收集待生成的 service（归档过滤 + x-grpc 注记 + Pascal 冲突检测）。
 * @returns {{name: string, svc: string, readonly: boolean, description: string}[]}
 */
function collectServices(store, opts) {
  const names = opts.resources || filterArchived(store.list());
  const seen = new Map();
  const out = [];
  for (const name of names) {
    const defn = typeof store.get === 'function' ? store.get(name) : null;
    const xg = (defn && defn['x-grpc']) || {};
    if (xg.hidden) continue; // spec/01 注记：模型级 hidden
    const svc = serviceName(name);
    if (seen.has(svc)) {
      throw new Error(`ERR_NAME_CONFLICT:模型 "${name}" 与 "${seen.get(svc)}" 的 service 名 "${svc}" 冲突（spec/01）`);
    }
    seen.set(svc, name);
    out.push({
      name,
      svc,
      readonly: !!xg.readonly, // spec/01 注记：模型级 readonly → 只出查询两段
      description: (defn && defn.description) || '',
    });
  }
  return out;
}

function renderService(s) {
  const lines = [];
  if (s.description) lines.push(`// ${s.description}`);
  lines.push(`service ${s.svc} {`);
  lines.push(`  // ${RPC_COMMENTS.list}`);
  lines.push(`  rpc List${s.svc} (ListRequest) returns (StoreReply);`);
  lines.push(`  // ${RPC_COMMENTS.get}`);
  lines.push(`  rpc Get${s.svc} (GetRequest) returns (StoreReply);`);
  if (!s.readonly) {
    lines.push(`  // ${RPC_COMMENTS.create}`);
    lines.push(`  rpc Create${s.svc} (CreateRequest) returns (StoreReply);`);
    lines.push(`  // ${RPC_COMMENTS.update}`);
    lines.push(`  rpc Update${s.svc} (UpdateRequest) returns (StoreReply);`);
    lines.push(`  // ${RPC_COMMENTS.remove}`);
    lines.push(`  rpc Delete${s.svc} (DeleteRequest) returns (StoreReply);`);
  }
  lines.push('}');
  return lines.join('\n');
}

/**
 * spec/01：schema → .proto 文本（纯构建，零 gRPC 依赖）。
 * @param {object} store 已 init + register 的 store 实例
 * @param {object} [opts]
 * @param {string[]} [opts.resources] 显式资源名；缺省取 store.list() 并过滤归档表
 */
function buildProto(store, opts = {}) {
  const services = collectServices(store, opts);
  return [
    '// 由 store-grpc 自动生成 —— 事实源：store 的 JSON schema（与 store-api / store-graphql 同源）',
    'syntax = "proto3";',
    '',
    `package ${PACKAGE};`,
    '',
    COMMON_MESSAGES,
    services.map(renderService).join('\n\n'),
    '',
  ].join('\n');
}

/** spec/01：exportProto ≡ buildProto（命名对齐 store-graphql 的 exportSDL） */
function exportProto(store, opts) {
  return buildProto(store, opts);
}

// ── spec/03：错误判定链（判定顺序双端一致，改动必须先改 spec）──

function storeCode(err) {
  return (err && err.code) || (err && err.name) || 'STORE_ERROR';
}

function errWith(code, message) {
  const e = new Error(message);
  e.code = code;
  return e;
}

/** 请求 JSON 字段解析（spec/02）：空串 ⇒ undefined；非法 JSON ⇒ 带码错误 */
function parseJsonField(raw, code, label) {
  if (raw == null || raw === '') return undefined;
  try {
    return JSON.parse(raw);
  } catch (e) {
    throw errWith(code, `${label} 不是合法 JSON: ${e.message}`);
  }
}

/** spec/02：必须为 JSON 对象编码（数组 / 标量 / 非法 JSON 拒绝）；空 ⇒ undefined（由调用方定缺省语义） */
function requireJsonObject(raw, label) {
  const v = parseJsonField(raw, 'INVALID_BODY', label);
  if (v !== undefined && (typeof v !== 'object' || v === null || Array.isArray(v))) {
    throw errWith('INVALID_BODY', `${label} 必须是 JSON 对象编码`);
  }
  return v;
}

/**
 * spec/03 错误 → { code, details, metadata }（metadata.trailer 携带 store-error-code）。
 * details 一律原 message 透传、取不到置空串（禁伪造）。
 */
function mapStoreError(err, PermissionErrorClass, grpc) {
  const message = err && err.message != null ? String(err.message) : '';
  const fail = (status, code, details) => {
    const metadata = new grpc.Metadata();
    if (code) metadata.set('store-error-code', code);
    return { code: status, details: details != null ? details : '', metadata };
  };
  if (err && err.code === 'NOT_FOUND') {
    return fail(grpc.status.NOT_FOUND, err.code, err.message);
  }
  if (err && (err.code === 'INVALID_PARAM' || err.code === 'INVALID_BODY')) {
    return fail(grpc.status.INVALID_ARGUMENT, err.code, err.message);
  }
  if (err && err.code === 'CONTEXT_ERROR') {
    return fail(grpc.status.UNAUTHENTICATED, 'CONTEXT_ERROR', err.message);
  }
  if (PermissionErrorClass && err instanceof PermissionErrorClass) {
    return fail(grpc.status.PERMISSION_DENIED, storeCode(err), err.message);
  }
  if (err && typeof err.message === 'string' && err.message.startsWith('ERR_PERMISSION:')) {
    return fail(grpc.status.PERMISSION_DENIED, 'ERR_PERMISSION', err.message);
  }
  if (err && typeof err.message === 'string' && err.message.startsWith('ERR_GQL_PARSE:')) {
    return fail(grpc.status.INVALID_ARGUMENT, 'GQL_PARSE', err.message.slice('ERR_GQL_PARSE:'.length));
  }
  return fail(grpc.status.INTERNAL, storeCode(err), err ? err.message : null);
}

// ── spec/02：rpc → store 调用 ──

function makeHandlers(store, s, opts, PermissionErrorClass, grpc) {
  const { name, svc, readonly } = s;
  const proj = schemaProjection(store, name);
  const oneParams = (id) => ({ c0: { [opts.idField]: id } });

  const wrap = (fn) => async (call, callback) => {
    try {
      if (opts.contextProvider) {
        let ctx;
        try {
          ctx = await opts.contextProvider(call.metadata);
        } catch (e) {
          // spec/02：PermissionError 类 / ERR_PERMISSION: 前缀 ⇒ PERMISSION_DENIED；其余 ⇒ UNAUTHENTICATED
          if (PermissionErrorClass && e instanceof PermissionErrorClass) throw e;
          if (e && typeof e.message === 'string' && e.message.startsWith('ERR_PERMISSION:')) throw e;
          throw errWith('CONTEXT_ERROR', e && e.message != null ? String(e.message) : '');
        }
        // spec/02：null/undefined 同样显式注入（setContext(null) 清除语义必须落地，
        // 有状态持有的运行时禁止残留上一请求上下文，防身份跨请求泄漏）
        await store.setContext(ctx != null ? ctx : null);
      }
      const data = await fn(call.request);
      // protobufjs 惯例：JS 侧字段名 camelCase（proto 文件保持 snake_case，py/go 客户端按各语言惯例）
      callback(null, { dataJson: JSON.stringify(data) });
    } catch (e) {
      callback(mapStoreError(e, PermissionErrorClass, grpc));
    }
  };

  const handlers = {
    [`List${svc}`]: wrap(async (req) => {
      const params = parseJsonField(req.paramsJson, 'INVALID_PARAM', 'params_json');
      // spec/02：q 缺失 → schema 投影；q 存在 → 投影完全由 q 决定，适配层不追加
      const gql = name + (req.q || proj);
      return store.query(gql, params);
    }),
    [`Get${svc}`]: wrap(async (req) => {
      const data = await store.queryOne(`${name}($condition: @c0)${proj}`, oneParams(req.id));
      if (data == null) throw errWith('NOT_FOUND', `记录不存在: ${opts.idField}=${req.id}`);
      return data;
    }),
  };
  if (!readonly) {
    handlers[`Create${svc}`] = wrap(async (req) => {
      const body = requireJsonObject(req.bodyJson, 'body_json');
      if (body === undefined) throw errWith('INVALID_BODY', 'body_json 必须是 JSON 对象编码');
      return store.insert(name, body);
    });
    handlers[`Update${svc}`] = wrap(async (req) => {
      const set = requireJsonObject(req.setJson, 'set_json');
      if (set === undefined) throw errWith('INVALID_BODY', 'set_json 必须是 JSON 对象编码');
      return store.update(name, { [opts.idField]: req.id }, set);
    });
    handlers[`Delete${svc}`] = wrap((req) => store.remove(name, { [opts.idField]: req.id }));
  }
  return handlers;
}

// ── spec/02：gRPC 承载（grpc-js，可选依赖；protobufjs 动态序列化，零 protoc）──

const REQ_TYPE = {
  List: 'ListRequest',
  Get: 'GetRequest',
  Create: 'CreateRequest',
  Update: 'UpdateRequest',
  Delete: 'DeleteRequest',
};

function requireGrpc() {
  try {
    // eslint-disable-next-line global-require
    return require('@grpc/grpc-js');
  } catch (e) {
    const err = new Error(
      'createServer 需要安装 @grpc/grpc-js（npm i @grpc/grpc-js）；或仅用 buildProto / exportProto 自行承载'
    );
    err.code = 'DEPENDENCY_MISSING';
    throw err;
  }
}

function createServer(store, opts = {}) {
  const grpc = requireGrpc();
  const protobuf = require('protobufjs');
  const idField = opts.idField || '_id';
  const protoStr = buildProto(store, opts);
  const services = collectServices(store, opts);
  const root = protobuf.parse(protoStr).root;
  const replyType = root.lookupType(`${PACKAGE}.StoreReply`);
  const reqTypes = {};
  for (const [verb, type] of Object.entries(REQ_TYPE)) reqTypes[verb] = root.lookupType(`${PACKAGE}.${type}`);

  const PermissionErrorClass =
    (opts.errors && opts.errors.PermissionError) || store.PermissionError || null;

  const server = new grpc.Server();
  for (const s of services) {
    const definition = {};
    const verbs = s.readonly ? ['List', 'Get'] : ['List', 'Get', 'Create', 'Update', 'Delete'];
    for (const verb of verbs) {
      const Req = reqTypes[verb];
      definition[`${verb}${s.svc}`] = {
        path: `/${PACKAGE}.${s.svc}/${verb}${s.svc}`,
        requestStream: false,
        responseStream: false,
        requestSerialize: (obj) => Buffer.from(Req.encode(Req.fromObject(obj == null ? {} : obj)).finish()),
        requestDeserialize: (buf) => Req.toObject(Req.decode(buf)),
        responseSerialize: (obj) => Buffer.from(replyType.encode(replyType.fromObject(obj == null ? {} : obj)).finish()),
        responseDeserialize: (buf) => replyType.toObject(replyType.decode(buf)),
      };
    }
    server.addService(definition, makeHandlers(store, { ...s }, { ...opts, idField }, PermissionErrorClass, grpc));
  }

  const shutdown = () => new Promise((resolve) => server.tryShutdown(resolve));
  if (opts.port == null) {
    return Promise.resolve({ server, proto: protoStr, port: null, shutdown });
  }
  const host = opts.host || '127.0.0.1';
  return new Promise((resolve, reject) => {
    server.bindAsync(`${host}:${opts.port}`, grpc.ServerCredentials.createInsecure(), (err, boundPort) => {
      if (err) reject(err);
      else resolve({ server, proto: protoStr, port: boundPort, shutdown });
    });
  });
}

module.exports = {
  filterArchived,
  serviceName,
  schemaProjection,
  collectServices,
  buildProto,
  exportProto,
  createServer,
  parseJsonField,
  requireJsonObject,
  mapStoreError,
  storeCode,
};
