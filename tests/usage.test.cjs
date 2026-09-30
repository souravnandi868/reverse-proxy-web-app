const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor() { this.children = []; this.dataset = {}; this.handlers = {}; this.textContent = ''; this.value = ''; this.classList = {add() {}, remove() {}}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {}
  addEventListener(event, callback) { this.handlers[event] = callback; }
  removeEventListener(event) { delete this.handlers[event]; }
}
const text = node => [node.textContent, ...node.children.map(text)].join(' ');
const tick = () => new Promise(resolve => setImmediate(resolve));
function harness() {
  const handlers = {}, requests = [], timers = [];
  const nodes = Object.fromEntries(['website-usage', 'usage-fqdn', 'usage-range', 'usage-charts', 'usage-sites', 'usage-status'].map(id => [id, new Element()]));
  nodes['website-usage'].dataset.url = '/usage/metrics/';
  nodes['usage-range'].value = '180';
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/js/usage.js'), 'utf8'), {
    document: {hidden: false, getElementById: id => nodes[id], createElement: () => new Element(),
      createElementNS: () => new Element(), addEventListener: (name, cb) => { handlers[name] = cb; }},
    AbortSignal, AbortController, URLSearchParams,
    setTimeout: (fn, delay) => { timers.push({fn, delay}); return timers.length; }, clearTimeout() {},
    fetch: (url, options) => new Promise(resolve => requests.push({url, signal: options.signal, resolve})),
  });
  return {nodes, requests, timers, handlers};
}
function respond(request, fqdn = 'one.example') {
  const sample = {requests: 2, errors: 1, rx: 100, tx: 200, response_ms: 12, error_percent: 50,
    rx_bps: 10, tx_bps: 20, time: '2026-10-01T12:00:00Z'};
  request.resolve({ok: true, headers: {get: () => 'application/json'}, json: async () => ({
    end: sample.time, total: sample, series: [sample], sites: [{...sample, fqdn, backend: '10.0.0.1:80'}],
  })});
}

test('usage renders graphs and comparison then schedules a three-second refresh', async () => {
  const h = harness();
  assert.equal(h.requests.length, 1);
  assert.equal(h.timers.length, 0);
  respond(h.requests[0]); await tick();
  assert.equal(h.nodes['usage-charts'].children.length, 4);
  assert.match(text(h.nodes['usage-sites']), /one.example.*10.0.0.1:80/);
  assert.match(text(h.nodes['usage-sites']), /50.0%/);
  assert.equal(h.timers[0].delay, 3000);
  h.handlers['app:before-render']();
  h.timers[0].fn();
  assert.equal(h.requests.length, 1);
});

test('changing FQDN aborts old request and ignores late responses', async () => {
  const h = harness();
  h.nodes['usage-fqdn'].value = 'two.example';
  h.nodes['usage-fqdn'].handlers.change();
  assert.equal(h.requests[0].signal.aborted, true);
  assert.match(h.requests[1].url, /fqdn=two.example/);
  respond(h.requests[1], 'two.example'); await tick();
  respond(h.requests[0], 'old.example'); await tick();
  assert.match(text(h.nodes['usage-sites']), /two.example/);
  assert.doesNotMatch(text(h.nodes['usage-sites']), /old.example/);
  assert.equal(h.timers.length, 1);
});

test('leaving during fetch prevents a late page update', async () => {
  const h = harness();
  h.handlers['app:before-render']();
  assert.equal(h.requests[0].signal.aborted, true);
  respond(h.requests[0]); await tick();
  assert.equal(h.nodes['usage-charts'].children.length, 0);
  assert.equal(h.timers.length, 0);
});
