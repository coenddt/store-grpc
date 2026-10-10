'use strict';

/**
 * 映射矩阵：每档错误语义 → gRPC 状态码（A3「可程序化区分」验收）。
 * 直接调纯函数 mapStoreError（无需真实 server/库），用例与 py/tests/test_error_matrix.py 同构。
 * 规范依据：spec/03-errors-context.md（Permission / NoContext 同属权限类 ⇒ PERMISSION_DENIED）。
 */

const { test } = require('node:test');
const assert = require('node:assert');
const grpc = require('@grpc/grpc-js');

const { mapStoreError } = require('../src/index');

// 显式 name（对齐 py 类名语义）：storeCode 链取 err.code → err.name，无 code 时落到 name
class PermissionError extends Error {
  constructor(message) {
    super(message);
    this.name = 'PermissionError';
  }
}

test('映射矩阵：Permission 档 → PERMISSION_DENIED', () => {
  const err = new PermissionError('RBAC 拒绝');
  const r = mapStoreError(err, PermissionError, grpc);
  assert.equal(r.code, grpc.status.PERMISSION_DENIED);
  assert.equal(r.metadata.get('store-error-code')[0], 'PermissionError');
});

test('映射矩阵：NoContext 档（machine code no_context）→ PERMISSION_DENIED', () => {
  const err = new Error('上下文缺失');
  err.code = 'no_context';
  const r = mapStoreError(err, PermissionError, grpc);
  assert.equal(r.code, grpc.status.PERMISSION_DENIED);
  assert.equal(r.metadata.get('store-error-code')[0], 'no_context');
});

test('映射矩阵：NoContext 档（字符串通道前缀 ERR_NO_CONTEXT:）→ PERMISSION_DENIED', () => {
  const r = mapStoreError(new Error('ERR_NO_CONTEXT:上下文缺失'), PermissionError, grpc);
  assert.equal(r.code, grpc.status.PERMISSION_DENIED);
  assert.equal(r.metadata.get('store-error-code')[0], 'no_context');
});

test('映射矩阵：Other 档 → INTERNAL 透传原文（禁静默）', () => {
  const r = mapStoreError(new Error('数据库连接失败'), PermissionError, grpc);
  assert.equal(r.code, grpc.status.INTERNAL);
  assert.equal(r.details, '数据库连接失败');
});