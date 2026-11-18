const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function load(name, mocks = {}, globals = {}) {
  const file = path.join(__dirname, '../src', name + '.ts');
  const source = fs.readFileSync(file, 'utf8').replaceAll('import.meta.env', '({})');
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2021,
  }}).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: n => mocks[n] ?? require(n),
    AbortController, setTimeout, clearTimeout, Uint8Array, ...globals }, { filename: file });
  return exports;
}

function timers() {
  const tasks = new Map();
  let next = 0;
  return { tasks, setTimeout(fn, delay) { const id = ++next; tasks.set(id, { fn, delay }); return id; },
    clearTimeout(id) { tasks.delete(id); }, async fire() {
      const [id, task] = tasks.entries().next().value;
      tasks.delete(id); await task.fn();
      await new Promise(resolve => setImmediate(resolve));
    }};
}

test('idempotency IDs work without secure-context crypto', () => {
  const { requestId } = load('requestId', {}, { crypto: undefined });
  const ids = new Set(Array.from({ length: 100 }, requestId));
  assert.equal(ids.size, 100);
  assert.match(requestId(), /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

test('session survives unavailable storage, and revoked identity stays cleared', () => {
  const localStorage = { getItem() { throw Error('blocked'); }, setItem() { throw Error('quota'); },
    removeItem() { throw Error('blocked'); }, get length() { throw Error('blocked'); } };
  const s = load('session', { vue: { reactive: x => x } }, { localStorage });
  s.saveSession(1, { memberId: 1, token: 'old', role: 'initiator', displayName: 'test', inviteToken: 'invite' });
  assert.equal(s.getSession(1).token, 'old');
  assert.ok(s.sessionNotice.message);
  s.clearInvalidToken('old');
  assert.equal(s.getSession(1), null);
  assert.equal(s.session.current, null);
  assert.equal(s.sessionNotice.rejoinUrl, '/join/1/invite');
});

test('401 clears only the failing identity; retained access preserves credentials', async () => {
  const cleared = [];
  let code = 'UNAUTHORIZED';
  const { api } = load('api', { './session': { clearInvalidToken: t => cleared.push(t) } }, {
    fetch: async () => ({ ok: false, status: 401, text: async () => JSON.stringify({ code, message: 'expired' }) }),
  });
  await assert.rejects(api.myState(1, 'expired'), e => e.status === 401);
  assert.deepEqual(cleared, ['expired']);
  code = 'PARTICIPATION_ENDED';
  await assert.rejects(api.myState(1, 'retained'), e => e.status === 401);
  assert.deepEqual(cleared, ['expired']);
});

test('timeout covers response body, aborts fetch, and releases timer', async () => {
  const clock = timers();
  const { api } = load('api', { './session': {} }, { ...clock,
    fetch: async (_, options) => ({ ok: true, status: 200, text: () => new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(Error('aborted')));
    }) }),
  });
  const pending = assert.rejects(api.myState(1, 'token'), e => e.code === 'REQUEST_TIMEOUT');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal([...clock.tasks.values()][0].delay, 15000);
  await clock.fire();
  await pending;
  assert.equal(clock.tasks.size, 0);
});

test('polling backs off after swallowed request failures, resets, and stops on unmount', async () => {
  const clock = timers();
  const api = { requestFailureCount: 0 };
  let unmount;
  let fail = true;
  const { usePolling } = load('polling', { './api': api, vue: { onUnmounted: cb => unmount = cb } },
    { ...clock, document: { visibilityState: 'visible' } });
  const polling = usePolling(async () => { if (fail) api.requestFailureCount++; }, () => true);
  polling.start();
  assert.equal([...clock.tasks.values()][0].delay, 4000);
  await clock.fire();
  assert.equal([...clock.tasks.values()][0].delay, 8000);
  await clock.fire();
  assert.equal([...clock.tasks.values()][0].delay, 16000);
  fail = false;
  await clock.fire();
  assert.equal([...clock.tasks.values()][0].delay, 4000);
  unmount();
  assert.equal(clock.tasks.size, 0);
});

test('stale refresh responses cannot overwrite a newer negotiation state', async () => {
  const pending = [];
  const state = { negotiationId: 1, current: { token: 'member', role: 'member' } };
  const { useNegotiationState } = load('useNegotiationState', {
    vue: { ref: value => ({ value }), computed: fn => ({ get value() { return fn(); } }), onUnmounted() {} },
    './session': { loadSession() {}, session: state },
    './api': { api: { publicView: () => new Promise(resolve => pending.push(resolve)),
      myState: async () => ({ member: {} }) }, ApiError: Error, errorMessage: e => e.message },
    './polling': { TERMINAL_STATUSES: new Set(), usePolling: () => ({}) },
  });
  const s = useNegotiationState(1);
  const old = s.refresh();
  const newer = s.refresh();
  pending[1]({ title: 'newer' }); await newer;
  pending[0]({ title: 'old' }); await old;
  assert.equal(s.pub.value.title, 'newer');
});
