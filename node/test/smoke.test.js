'use strict';

/**
 * 冒烟测试 — mock store + 真实 grpc-js server（本地随机端口，不连真实库）。
 * 用例语义与 ../conformance/cases/users-crud.json 对齐（spec 唯一事实源）。
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const {
  filterArchived,
  buildProto,
  exportProto,
  createServer,
} = require('../src/index');

const grpc = require('@grpc/grpc-js');
const protobuf = require('protobufjs');

const DEFN = {
  name: 'User',
  description: '用户表：平台账号主档',
  fields: {
    _id: { type: 'string', description: '主键，u 前缀' },
    name: { type: 'string' },
    age: { type: 'int' },
    profile: { type: 'object', description: '个人资料', fields: { bio: { type: 'string' } } },
  },
};

const REQ_TYPE_BY_RPC = {
  List: 'ListRequest',
  Get: 'GetRequest',
  Create: 'CreateRequest',
  Update: 'UpdateRequest',
  Delete: 'DeleteRequest',
};

// 内存版 store：query 按「条件匹配」，记录 GQL 串供断言投影透传；setContext 可注入 spy
function makeMockStore() {
  const rows = [];
  const gqlLog = [];
  let lastCtx = 'initial-not-cleaned';
  return {
    rows,
    gqlLog,
    // 实例 name 显式化（class extends Error 的实例 name 默认继承 'Error'；真实 store 权限类有稳定码/name）
    PermissionError: class PermissionError extends Error {
      get name() { return 'PermissionError'; }
    },
    lastCtx: () => lastCtx,
    list: () => ['User'],
    get: () => DEFN,
    async query(gql, params) {
      gqlLog.push({ gql, params });
      const cond = params && params.c0;
      return rows.filter((r) => !cond || Object.entries(cond).every(([k, v]) => r[k] === v));
    },
    async queryOne(gql, params) {
      gqlLog.push({ gql, params });
      const cond = params && params.c0;
      return rows.find((r) => Object.entries(cond).every(([k, v]) => r[k] === v)) || null;
    },
    async insert(_name, data) {
      const doc = { _id: `u${rows.length + 1}`, ...data };
      rows.push(doc);
      return doc;
    },
    async update(_name, cond, data) {
      const row = rows.find((r) => Object.entries(cond).every(([k, v]) => r[k] === v));
      Object.assign(row, data);
      return row;
    },
    async remove(_name, cond) {
      const i = rows.findIndex((r) => Object.entries(cond).every(([k, v]) => r[k] === v));
      if (i >= 0) rows.splice(i, 1);
      return i >= 0 ? 1 : 0;
    },
    async setContext(ctx) {
      lastCtx = ctx;
    },
  };
}

// 用 server 暴露的 proto 字符串构造 client（与生产客户端同路径：拿 .proto → 生成/调用）
async function startWithClient(store, opts = {}) {
  const svc = opts.svc || 'User';
  const verbs = opts.readonly ? ['List', 'Get'] : ['List', 'Get', 'Create', 'Update', 'Delete'];
  const { port, proto, shutdown } = await createServer(store, { port: 0, ...opts });
  const root = protobuf.parse(proto).root;
  const methods = {};
  for (const verb of verbs) {
    const rpc = `${verb}${svc}`;
    const Req = root.lookupType(`store.v0.${REQ_TYPE_BY_RPC[verb]}`);
    const Reply = root.lookupType('store.v0.StoreReply');
    methods[rpc] = {
      path: `/store.v0.${svc}/${rpc}`,
      requestStream: false,
      responseStream: false,
      requestSerialize: (o) => Buffer.from(Req.encode(Req.fromObject(o || {})).finish()),
      requestDeserialize: (b) => Req.toObject(Req.decode(b), ),
      responseSerialize: (o) => Buffer.from(Reply.encode(Reply.fromObject(o || {})).finish()),
      responseDeserialize: (b) => Reply.toObject(Reply.decode(b), ),
    };
  }
  const Client = grpc.makeGenericClientConstructor(methods, svc);
  const client = new Client(`127.0.0.1:${port}`, grpc.credentials.createInsecure());
  const call = (rpc, request) =>
    new Promise((resolve) => {
      client[rpc](request || {}, (err, reply) => {
        if (err) resolve({ error: err, trailer: err.metadata });
        else resolve({ reply });
      });
    });
  return { port, proto, client, call, shutdown: async () => { client.close(); await shutdown(); } };
}

test('filterArchived 归档表过滤（spec/01）', () => {
  assert.deepEqual(filterArchived(['User', 'UserDeleted', 'Log']), ['User', 'Log']);
});

test('buildProto：生成面 / 命名 / description 透传（spec/01）', () => {
  const store = makeMockStore();
  const proto = buildProto(store);
  assert.match(proto, /syntax = "proto3";/);
  assert.match(proto, /package store\.v0;/);
  assert.match(proto, /service User \{/);
  assert.match(proto, /rpc ListUser \(ListRequest\) returns \(StoreReply\);/);
  assert.match(proto, /rpc GetUser \(GetRequest\) returns \(StoreReply\);/);
  assert.match(proto, /rpc CreateUser \(CreateRequest\) returns \(StoreReply\);/);
  assert.match(proto, /rpc UpdateUser \(UpdateRequest\) returns \(StoreReply\);/);
  assert.match(proto, /rpc DeleteUser \(DeleteRequest\) returns \(StoreReply\);/);
  assert.match(proto, /\/\/ 用户表：平台账号主档/); // description 透传（spec/01）
  assert.equal(exportProto(store), proto); // exportProto ≡ buildProto（spec/01）
});

test('buildProto：x-grpc hidden / readonly 注记（spec/01）', () => {
  const store = makeMockStore();
  store.list = () => ['User', 'SecretLog', 'AuditEvent'];
  store.get = (n) =>
    n === 'User' ? DEFN
      : n === 'SecretLog' ? { ...DEFN, name: 'SecretLog', 'x-grpc': { hidden: true } }
        : { ...DEFN, name: 'AuditEvent', 'x-grpc': { readonly: true } };
  const proto = buildProto(store);
  assert.doesNotMatch(proto, /SecretLog/);
  assert.match(proto, /service AuditEvent \{/);
  assert.match(proto, /rpc ListAuditEvent/);
  assert.doesNotMatch(proto, /rpc CreateAuditEvent/); // readonly → 只出查询两段
});

test('buildProto：PascalCase 冲突构建期报错（spec/01）', () => {
  const store = makeMockStore();
  store.list = () => ['user', 'User'];
  store.get = () => DEFN;
  assert.throws(() => buildProto(store), /ERR_NAME_CONFLICT/);
});

test('五段 rpc 全链路 + q 投影透传（conformance users-crud 对齐）', async () => {
  const store = makeMockStore();
  const h = await startWithClient(store);
  try {
    // CreateUser
    const created = await h.call('CreateUser', { bodyJson: JSON.stringify({ name: 'alice', age: 30 }) });
    assert.equal(created.reply.dataJson, '{"_id":"u1","name":"alice","age":30}');

    // ListUser（q 透传 + params_json）
    const listed = await h.call('ListUser', {
      q: '($condition: @c0) { name, age }',
      paramsJson: JSON.stringify({ c0: { age: 30 } }),
    });
    assert.equal(listed.reply.dataJson, '[{"_id":"u1","name":"alice","age":30}]');
    assert.equal(store.gqlLog.at(-1).gql, 'User($condition: @c0) { name, age }'); // q 原样透传，适配层不追加投影

    // ListUser（q 缺省 → schema 投影拼接）
    await h.call('ListUser', {});
    assert.equal(store.gqlLog.at(-1).gql, 'User { _id, name, age, profile }');

    // GetUser
    const got = await h.call('GetUser', { id: 'u1' });
    assert.deepEqual(JSON.parse(got.reply.dataJson), { _id: 'u1', name: 'alice', age: 30 });

    // UpdateUser（返回 store.update 原始结果，不做二次回读——spec/02 对齐 REST）
    const updated = await h.call('UpdateUser', { id: 'u1', setJson: JSON.stringify({ age: 31 }) });
    assert.deepEqual(JSON.parse(updated.reply.dataJson), { _id: 'u1', name: 'alice', age: 31 });

    // DeleteUser
    const del = await h.call('DeleteUser', { id: 'u1' });
    assert.equal(del.reply.dataJson, '1');

    // 删除后 GetUser ⇒ NOT_FOUND + trailer store-error-code（spec/03）
    const missing = await h.call('GetUser', { id: 'u1' });
    assert.equal(missing.error.code, grpc.status.NOT_FOUND);
    assert.equal(missing.error.details, '记录不存在: _id=u1');
    assert.equal(missing.trailer.get('store-error-code')[0], 'NOT_FOUND');
  } finally {
    await h.shutdown();
  }
});

test('错误映射：INVALID_BODY / INVALID_PARAM / GQL_PARSE / PERMISSION / INTERNAL（spec/03 判定链）', async () => {
  const store = makeMockStore();
  const h = await startWithClient(store);
  try {
    // body_json 数组 ⇒ INVALID_ARGUMENT + INVALID_BODY
    const badBody = await h.call('CreateUser', { bodyJson: '[1,2]' });
    assert.equal(badBody.error.code, grpc.status.INVALID_ARGUMENT);
    assert.equal(badBody.trailer.get('store-error-code')[0], 'INVALID_BODY');

    // body_json 非法 JSON ⇒ INVALID_BODY
    const badJson = await h.call('CreateUser', { bodyJson: '{oops' });
    assert.equal(badJson.trailer.get('store-error-code')[0], 'INVALID_BODY');

    // params_json 非法 JSON ⇒ INVALID_PARAM
    const badParams = await h.call('ListUser', { paramsJson: 'not-json' });
    assert.equal(badParams.error.code, grpc.status.INVALID_ARGUMENT);
    assert.equal(badParams.trailer.get('store-error-code')[0], 'INVALID_PARAM');

    // core GQL 解析失败（ERR_GQL_PARSE: 前缀）⇒ INVALID_ARGUMENT + GQL_PARSE，details 剥前缀
    store.query = async () => { throw new Error('ERR_GQL_PARSE:意外的 token'); };
    const gqlParse = await h.call('ListUser', { q: '($bogus' });
    assert.equal(gqlParse.error.code, grpc.status.INVALID_ARGUMENT);
    assert.equal(gqlParse.trailer.get('store-error-code')[0], 'GQL_PARSE');
    assert.equal(gqlParse.error.details, '意外的 token');

    // ERR_PERMISSION: 前缀 ⇒ PERMISSION_DENIED（无 PermissionError 类来源时的兜底判定）
    store.query = async () => { throw new Error('ERR_PERMISSION:无访问权限'); };
    const denied = await h.call('ListUser', {});
    assert.equal(denied.error.code, grpc.status.PERMISSION_DENIED);
    assert.equal(denied.trailer.get('store-error-code')[0], 'ERR_PERMISSION');

    // PermissionError 类（store.PermissionError 来源）⇒ PERMISSION_DENIED，码取 storeCode
    store.query = async () => { throw new store.PermissionError('RBAC 拒绝'); };
    const byClass = await h.call('ListUser', {});
    assert.equal(byClass.error.code, grpc.status.PERMISSION_DENIED);
    assert.equal(byClass.trailer.get('store-error-code')[0], 'PermissionError');

    // 其余 store 错误 ⇒ INTERNAL，message 原样透传（禁掩盖）
    store.query = async () => { throw new Error('数据库连接失败'); };
    const internal = await h.call('ListUser', {});
    assert.equal(internal.error.code, grpc.status.INTERNAL);
    assert.equal(internal.error.details, '数据库连接失败');
    assert.equal(internal.trailer.get('store-error-code')[0], 'Error'); // storeCode 链 err.code→err.name→STORE_ERROR，与 REST 同（new Error().name='Error'）
  } finally {
    await h.shutdown();
  }
});

test('contextProvider：注入 / null 显式清除 / 403-401 分类（spec/02）', async () => {
  const store = makeMockStore();
  const h = await startWithClient(store, {
    contextProvider: async (metadata) => {
      const user = metadata.get('x-user')[0];
      if (user === 'bad') throw new Error('ERR_PERMISSION:无访问权限');
      if (user === 'broken') throw new Error('上下文钩子故障');
      if (user === 'anon') return null; // 显式空上下文
      return { user };
    },
  });
  try {
    const raw = (user) =>
      new Promise((resolve) => {
        const md = new grpc.Metadata();
        md.set('x-user', user);
        h.client.ListUser({ q: ' { _id }' }, md, (err) => resolve(err));
      });

    // 注入：setContext 收到 provider 返回的对象
    await raw('alice');
    assert.deepEqual(store.lastCtx(), { user: 'alice' });

    // ERR_PERMISSION: 前缀 ⇒ PERMISSION_DENIED，且 store 调用不发生（判定在进 handler 语义之前）
    const before = store.gqlLog.length;
    const denied = await raw('bad');
    assert.equal(denied.code, grpc.status.PERMISSION_DENIED);
    assert.equal(store.gqlLog.length, before); // store 未被调用

    // 非权限类 ⇒ UNAUTHENTICATED，message 原样透传
    const broken = await raw('broken');
    assert.equal(broken.code, grpc.status.UNAUTHENTICATED);
    assert.equal(broken.details, '上下文钩子故障');

    // 返回 null ⇒ 显式 setContext(null)（清除语义落地，防上一请求上下文残留）
    await raw('anon');
    assert.equal(store.lastCtx(), null);
  } finally {
    await h.shutdown();
  }
});

test('readonly 模型只暴露查询两段（spec/01 注记 → 运行时）', async () => {
  const store = makeMockStore();
  store.list = () => ['AuditEvent'];
  store.get = (n) => ({ ...DEFN, name: n, 'x-grpc': { readonly: true } });
  const h = await startWithClient(store, { svc: 'AuditEvent', readonly: true });
  try {
    const listed = await h.call('ListAuditEvent', {});
    assert.equal(listed.reply.dataJson, '[]');
    const got = await h.call('GetAuditEvent', { id: 'x' });
    assert.equal(got.error.code, grpc.status.NOT_FOUND);
  } finally {
    await h.shutdown();
  }
});

test('proto 落盘即为合法 .proto 文件（exportProto 出口契约）', async () => {
  const store = makeMockStore();
  const proto = exportProto(store);
  const tmp = path.join(__dirname, 'tmp-proto-check');
  fs.mkdirSync(tmp, { recursive: true });
  const file = path.join(tmp, 'store_grpc.proto');
  fs.writeFileSync(file, proto);
  const parsed = protobuf.parse(fs.readFileSync(file, 'utf8')).root; // 可再解析 = 合法 IDL
  assert.ok(parsed.lookupType('store.v0.ListRequest'));
  fs.rmSync(tmp, { recursive: true, force: true });
});
