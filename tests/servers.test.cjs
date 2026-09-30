const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor() { this.children = []; this.dataset = {}; this.textContent = ''; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren() { this.children = []; }
  setAttribute(key, value) { (this.attributes ||= {})[key] = value; }
  querySelectorAll() { return []; }
}
const textOf = node => [node.textContent, ...node.children.map(textOf)].join(' ');

async function render(rates, history = []) {
  const nodes = Object.fromEntries(['resource-monitor', 'server-cards', 'monitor-status'].map(id => [id, new Element()]));
  const metrics = {
    cpu: 10, ram: {used: 100, total: 1000, percent: 10}, disks: [],
    interfaces: [{name: 'eth0', rx: 999999999, tx: 888888888, speed_mbps: 1000}], ...rates,
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/js/servers.js'), 'utf8'), {
    document: {addEventListener() {}, getElementById: id => nodes[id], createElement: () => new Element(), createElementNS: () => new Element()},
    window: {setTimeout() {}}, AbortSignal, AbortController, clearTimeout, clearInterval,
    fetch: async () => ({ok: true, headers: {get: () => 'application/json'}, json: async () => ({servers: [
      {address: '10.0.0.4', name: 'NGINX', status: 'live', received_at: '2026-09-13T12:00:00Z', metrics, history},
    ]})}),
  });
  await new Promise(resolve => setImmediate(resolve));
  const card = nodes['server-cards'].children[0];
  const resources = card.children.find(node => node.className === 'resource-grid');
  assert.ok(resources, textOf(nodes['monitor-status']));
  return {graph: textOf(resources.children[3]), card: textOf(card), cpuChart: resources.children[0].children[3]};
}

test('first render and reopening load saved history with gaps for missing reports', async () => {
  const history = [
    {time: '2026-09-13T11:58:00Z', cpu: 10},
    {time: '2026-09-13T11:58:10Z', cpu: 20},
    {time: '2026-09-13T12:00:00Z', cpu: 30},
  ];
  for (let i = 0; i < 2; i++) {
    const {cpuChart} = await render({}, history);
    assert.equal(cpuChart.children.length, 2);
    assert.equal(cpuChart.children[0].attributes.points.split(' ').length, 2);
    assert.equal(cpuChart.children[1].attributes.points.split(' ').length, 1);
  }
});

test('external graph uses log byte rates while NIC data stays diagnostic', async () => {
  const {graph, card} = await render({nginx_rx_bps: 2048, nginx_tx_bps: 3145728});
  assert.match(graph, /External NGINX Traffic/);
  assert.match(graph, /Receive: 2.00 KiB\/s/);
  assert.match(graph, /Send: 3.00 MiB\/s/);
  assert.match(card, /NIC diagnostics \(host traffic\)/);
  assert.match(card, /eth0/);
});

test('missing and unavailable log metrics never fall back to NIC rates', async () => {
  for (const rates of [{}, {nginx_rx_bps: null, nginx_tx_bps: null}]) {
    const {graph} = await render(rates);
    assert.match(graph, /Unavailable/);
    assert.doesNotMatch(graph, /Receive:/);
  }
});

test('zero log traffic remains a valid zero rate', async () => {
  const {graph} = await render({nginx_rx_bps: 0, nginx_tx_bps: 0});
  assert.match(graph, /Receive: 0.00 KiB\/s/);
  assert.match(graph, /Send: 0.00 KiB\/s/);
});
