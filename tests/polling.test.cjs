const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function harness(script, historical = false) {
  const handlers = {};
  const timers = [];
  const requests = [];
  const element = () => ({dataset: {url: '/metrics/', historical: String(historical)},
    innerHTML: 'original', setAttribute() {}, after() {}, remove() {},
    replaceChildren() {}, append() {}, querySelectorAll: () => []});
  const nodes = Object.fromEntries(['traffic-rows', 'traffic-refresh-status',
    'resource-monitor', 'server-cards', 'monitor-status'].map(id => [id, element()]));
  const schedule = (fn, delay) => { timers.push({fn, delay}); return timers.length; };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/js', script), 'utf8'), {
    document: {hidden: false, addEventListener: (name, fn) => { handlers[name] = fn; },
      removeEventListener() {}, getElementById: id => nodes[id], createElement: element,
      querySelectorAll: () => [element()], querySelector: element},
    window: {setInterval: schedule, setTimeout: schedule, addEventListener() {}, location: {href: '/'}},
    clearTimeout() {}, clearInterval() {}, AbortController, AbortSignal,
    fetch: (url, options) => new Promise(resolve => requests.push({resolve, signal: options.signal})),
  });
  return {handlers, timers, requests, nodes};
}

for (const [script, delay] of [['traffic.js', 3000], ['live-pages.js', 5000]]) {
  test(`${script} suppresses overlapping requests and aborts on navigation`, async () => {
    const h = harness(script);
    assert.equal(h.timers[0].delay, delay);
    h.timers[0].fn();
    h.timers[0].fn();
    assert.equal(h.requests.length, 1);
    h.handlers['app:before-render']();
    assert.equal(h.requests[0].signal.aborted, true);
    h.timers[0].fn();
    assert.equal(h.requests.length, 1);
    h.requests[0].resolve({ok: true, headers: {get: () => 'application/json'}, json: async () => ({html: 'late'})});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.nodes['traffic-rows'].innerHTML, 'original');
  });
}

test('resource polling schedules 3 seconds only after a request finishes', async () => {
  const h = harness('servers.js');
  assert.equal(h.requests.length, 1);
  assert.equal(h.timers.length, 0);
  h.requests[0].resolve({ok: true, headers: {get: () => 'application/json'}, json: async () => ({servers: []})});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.timers.length, 1);
  assert.equal(h.timers[0].delay, 3000);
  h.handlers['app:before-render']();
  h.timers[0].fn();
  assert.equal(h.requests.length, 1);
});

test('historical traffic pages do not poll or replace rows', () => {
  const h = harness('traffic.js', true);
  assert.equal(h.requests.length, 0);
  assert.equal(h.timers.length, 0);
});
