'use strict';

/**
 * conformance runner — 读取共享用例 JSON（../../conformance/cases/*.json）逐项执行，
 * 与 py 端 runner 断言同一份文件（spec 唯一事实源）。
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const grpc = require('@grpc/grpc-js');
const protobuf = require('protobufjs');
const { createServer } = require('../src/index');

const CASES_DIR = path.join(__dirname, '..', '..', 'conformance', 'cases');

const REQ_FIELD_BY_RPC = {
  List: ['q', 'params'], // params 通用形状 → paramsJson
  Get: ['id'],
  Create: ['body'],
  Update: ['id', 'set'],
  Delete: ['id'],
};

const STATUS_BY_NAME = {
  INVALID_ARGUMENT: grpc.status.INVALID_ARGUMENT,
  UNAUTHENTICATED: grpc.status.UNAUTHENTICATED,
  PERMISSION_DENIED: grpc.status.PERMISSION_DENIED,
  NOT_FOUND: grpc.status.NOT_FOUND,
  INTERNAL: grpc.status.INTERNAL,
};

function makeMockStore(defn) {
  const rows = [];
  return {
    list: () => [defn.name],
    get: () => defn,
    async query(gql, params) {
      // 模拟 core 的 ERR_GQL_PARSE: 前缀契约：括号不配平即解析失败（conformance GQL_PARSE 用例依赖）
      const open = (gql.match(/\(/g) || []).length;
      const close = (gql.match(/\)/g) || []).length;
      if (open !== close) throw new Error('ERR_GQL_PARSE:括号不配平');
      const cond = params && params.c0;
      return rows.filter((r) => !cond || Object.entries(cond).every(([k, v]) => r[k] === v));
    },
    async queryOne(gql, params) {
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
    async setContext() {},
  };
}

for (const file of fs.readdirSync(CASES_DIR).filter((f) => f.endsWith('.json'))) {
  const spec = JSON.parse(fs.readFileSync(path.join(CASES_DIR, file), 'utf8'));

  test(`conformance: ${spec.name}（${file}）`, async () => {
    const svc = spec.service;
    const store = makeMockStore({ name: svc, fields: { _id: { type: 'string' }, name: { type: 'string' }, age: { type: 'int' } } });
    const { port, proto, shutdown } = await createServer(store, { port: 0 });
    try {
      const root = protobuf.parse(proto).root;
      const REQ_TYPE = { List: 'ListRequest', Get: 'GetRequest', Create: 'CreateRequest', Update: 'UpdateRequest', Delete: 'DeleteRequest' };
      const Reply = root.lookupType('store.v0.StoreReply');
      const methods = {};
      for (const [verb, type] of Object.entries(REQ_TYPE)) {
        const Req = root.lookupType(`store.v0.${type}`);
        const rpc = `${verb}${svc}`;
        methods[rpc] = {
          path: `/store.v0.${svc}/${rpc}`,
          requestStream: false,
          responseStream: false,
          requestSerialize: (o) => Buffer.from(Req.encode(Req.fromObject(o || {})).finish()),
          requestDeserialize: (b) => Req.toObject(Req.decode(b)),
          responseSerialize: (o) => Buffer.from(Reply.encode(Reply.fromObject(o || {})).finish()),
          responseDeserialize: (b) => Reply.toObject(Reply.decode(b)),
        };
      }
      const Client = grpc.makeGenericClientConstructor(methods, svc);
      const client = new Client(`127.0.0.1:${port}`, grpc.credentials.createInsecure());

      const invoke = (rpc, request) =>
        new Promise((resolve) => {
          client[rpc](request, (err, reply) => {
            if (err) resolve({ error: err, trailer: err.metadata });
            else resolve({ reply });
          });
        });

      let createdId = null;
      for (const [i, step] of spec.steps.entries()) {
        const verb = step.rpc.replace(/^(List|Get|Create|Update|Delete).*/, '$1');
        const rpc = step.rpc;
        // 通用形状 → node 侧 camelCase 字段（spec/01 语言惯例）；字符串值原样传递（预编码/非法样本），对象才 stringify
        const request = {};
        if (step.request) {
          if (step.request.q != null) request.q = step.request.q;
          if (step.request.id != null) request.id = step.request.id;
          if (step.request.params != null) {
            request.paramsJson = typeof step.request.params === 'string' ? step.request.params : JSON.stringify(step.request.params);
          }
          if (step.request.body != null) request.bodyJson = JSON.stringify(step.request.body);
          if (step.request.set != null) request.setJson = JSON.stringify(step.request.set);
        }
        if (step.useCreatedId && request.id == null) request.id = createdId;

        const result = await invoke(rpc, request);

        if (step.expect.ok) {
          assert.ok(!result.error, `step#${i} ${rpc} 应成功: ${result.error && result.error.details}`);
          const data = JSON.parse(result.reply.dataJson);
          if (step.expect.dataKeys) {
            assert.deepEqual(Object.keys(data).filter((k) => step.expect.dataKeys.includes(k)).sort(), [...step.expect.dataKeys].sort(), `step#${i} dataKeys`);
          }
          if (step.expect.listLength != null) assert.equal(data.length, step.expect.listLength, `step#${i} listLength`);
          if (i === 0) createdId = JSON.parse(result.reply.dataJson)._id; // 首步建行 → 后续 useCreatedId
        } else {
          const expectStatus = STATUS_BY_NAME[step.expect.statusCode];
          assert.ok(result.error, `step#${i} ${rpc} 应失败`);
          assert.equal(result.error.code, expectStatus, `step#${i} status`);
          assert.equal(result.trailer.get('store-error-code')[0], step.expect.errorCode, `step#${i} errorCode`);
        }
      }
      client.close();
    } finally {
      await shutdown();
    }
  });
}
