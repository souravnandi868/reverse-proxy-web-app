(() => {
  const root = document.getElementById('resource-monitor');
  const cards = document.getElementById('server-cards');
  const status = document.getElementById('monitor-status');
  const history = new Map();
  const labels = {live: 'Live', stale: 'Stale — agent not reporting', waiting: 'Waiting for metrics', not_configured: 'Monitoring not configured'};
  const bytes = n => {
    const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(1)} ${units[i]}`;
  };
  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function chart(series, colors, percent) {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 300 90');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'Recent resource measurements');
    const ceiling = percent ? 100 : Math.max(1, ...series.flat());
    series.forEach((values, i) => {
      const line = document.createElementNS(ns, 'polyline');
      line.setAttribute('points', values.map((v, j) => `${j * 296 / Math.max(1, values.length - 1) + 2},${86 - v / ceiling * 82}`).join(' '));
      line.setAttribute('fill', 'none');
      line.setAttribute('stroke', colors[i]);
      line.setAttribute('stroke-width', '2');
      svg.append(line);
    });
    return svg;
  }
  function resource(title, value, detail, series, colors, percentage) {
    const node = el('div', undefined, 'resource');
    node.append(el('h3', title), el('strong', value), el('small', detail));
    node.append(chart(series, colors, percentage !== undefined));
    if (percentage !== undefined) {
      const meter = el('meter');
      meter.min = 0; meter.max = 100; meter.value = percentage;
      meter.setAttribute('aria-label', `${title} ${percentage.toFixed(1)} percent`);
      node.append(meter);
    }
    return node;
  }
  function render(servers) {
    cards.replaceChildren();
    if (!servers.length) cards.append(el('p', 'No servers yet. Add a reverse proxy or enroll a monitoring agent.', 'panel monitor-empty'));
    const known = new Set(servers.map(s => s.address));
    for (const key of history.keys()) if (!known.has(key)) history.delete(key);
    servers.forEach(server => {
      const card = el('section', undefined, `panel server-card ${server.status}`);
      const heading = el('div', undefined, 'server-title');
      heading.append(el('h2', server.address), el('span', labels[server.status], `server-state ${server.status}`));
      card.append(heading, el('p', server.domains.join(', ') || 'Monitored server', 'server-subtitle'));
      cards.append(card);
      const m = server.metrics;
      if (!m) {
        card.append(el('p', 'Resource measurements will appear when this server’s monitoring agent connects.', 'monitor-empty'));
        return;
      }
      card.append(el('p', `Last received: ${new Date(server.received_at).toLocaleString()}`, 'server-subtitle'));
      let h = history.get(server.address) || {time: null, samples: []};
      const disk = Math.max(0, ...m.disks.map(d => d.percent));
      const rx = m.interfaces.reduce((sum, n) => sum + n.rx, 0);
      const tx = m.interfaces.reduce((sum, n) => sum + n.tx, 0);
      if (server.status === 'live' && h.time !== server.received_at) {
        if (h.time && Date.parse(server.received_at) - Date.parse(h.time) > 30000) h.samples = [];
        h.samples.push({cpu: m.cpu, ram: m.ram.percent, disk, rx, tx});
        h.samples = h.samples.slice(-60); h.time = server.received_at;
      }
      history.set(server.address, h);
      const values = key => h.samples.map(s => s[key]);
      const grid = el('div', undefined, 'resource-grid');
      grid.append(resource('CPU', `${m.cpu.toFixed(1)}%`, 'Total processor utilization', [values('cpu')], ['#0875df'], m.cpu));
      grid.append(resource('RAM', `${m.ram.percent.toFixed(1)}%`, `${bytes(m.ram.used)} / ${bytes(m.ram.total)}`, [values('ram')], ['#15986a'], m.ram.percent));
      grid.append(resource('Storage', m.disks.length ? `${disk.toFixed(1)}%` : 'Unavailable', 'Most-used reported filesystem', [values('disk')], ['#d68b00'], disk));
      grid.append(resource('Network', `${bytes(rx)}/s`, `Receive (blue) · Send (purple): ${bytes(tx)}/s; auto-scaled graph`, [values('rx'), values('tx')], ['#0875df', '#8b5cf6']));
      card.append(grid);
      const devices = el('div', undefined, 'server-devices');
      const disks = el('div'); disks.append(el('h3', 'Storage by mount'));
      m.disks.forEach(d => {
        const row = el('div', undefined, 'device-row');
        row.append(el('span', d.mount), el('span', `${bytes(d.used)} / ${bytes(d.total)} (${d.percent.toFixed(1)}%)`)); disks.append(row);
      });
      const network = el('div'); network.append(el('h3', 'Network by interface'));
      m.interfaces.forEach(n => {
        const utilization = n.speed_mbps > 0 ? `${(Math.max(n.rx, n.tx) * 8 / (n.speed_mbps * 1e6) * 100).toFixed(1)}% of ${n.speed_mbps} Mbps link` : 'Link capacity unknown';
        const row = el('div', undefined, 'device-row');
        row.append(el('span', n.name), el('span', `RX ${bytes(n.rx)}/s · TX ${bytes(n.tx)}/s · ${utilization}`)); network.append(row);
      });
      devices.append(disks, network); card.append(devices);
    });
  }
  async function poll() {
    try {
      const response = await fetch(root.dataset.url, {cache: 'no-store', signal: AbortSignal.timeout(8000)});
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error();
      const data = await response.json(); render(data.servers);
      status.textContent = `Updated ${new Date().toLocaleTimeString()}`;
    } catch {
      status.textContent = 'Updates unavailable. Check your connection or sign in again.';
      cards.querySelectorAll('.server-card').forEach(card => {
        card.classList.add('stale');
        const badge = card.querySelector('.server-state');
        badge.classList.remove('live'); badge.textContent = 'Updates unavailable';
      });
    } finally { window.setTimeout(poll, 5000); }
  }
  poll();
})();
