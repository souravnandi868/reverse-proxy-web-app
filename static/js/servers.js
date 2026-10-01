(() => {
  const updateView = globalThis.updateLiveView || (update => update());
  let cleanup = () => {};
  function start() {
    cleanup();
    const root = document.getElementById('resource-monitor');
    const cards = document.getElementById('server-cards');
    const status = document.getElementById('monitor-status');
    if (!root) return;
    let stopped = false;
    let timer;
    const range = document.getElementById('resource-range');
    let latestServers = [];
    const changeRange = () => render(latestServers);
    range?.addEventListener('change', changeRange);
    const controller = new AbortController();
    cleanup = () => { stopped = true; clearTimeout(timer); clearInterval(timer); controller.abort(); range?.removeEventListener('change', changeRange); };
    const labels = {live: 'Live', stale: 'Stale — agent not reporting', waiting: 'Waiting for metrics', not_configured: 'Monitoring not configured'};
    const bytes = n => {
      const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
      let i = 0;
      while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
      return `${n.toFixed(1)} ${units[i]}`;
    };
    const rate = n => n >= 1024 * 1024 ? `${(n / (1024 * 1024)).toFixed(2)} MiB/s` : `${(n / 1024).toFixed(2)} KiB/s`;
    function el(tag, text, className) {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (className) node.className = className;
      return node;
    }
    function chart(series, colors, percent, format, title) {
      const ns = 'http://www.w3.org/2000/svg';
      const svg = document.createElementNS(ns, 'svg');
      const width = document.documentElement?.clientWidth < 540 ? 320 : 600;
      svg.setAttribute('viewBox', `0 0 ${width} 170`);
      svg.setAttribute('preserveAspectRatio', 'none');
      svg.setAttribute('role', 'img');
      svg.setAttribute('aria-label', `${title}: recent resource measurements`);
      const ceiling = percent ? 100 : Math.max(1, ...series.flat().map(s => s.value).filter(Number.isFinite));
      const svgNode = (tag, attrs) => {
        const element = document.createElementNS(ns, tag);
        Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, value));
        return element;
      };
      for (let tick = 0; tick <= 4; tick++) {
        const y = 155 - tick * 35;
        svg.append(svgNode('line', {x1: 80, x2: width - 10, y1: y, y2: y, stroke: '#e7edf5', 'stroke-dasharray': '3 5'}));
        const label = svgNode('text', {x: 72, y: y + 4, 'text-anchor': 'end', fill: '#7b8ba2', 'font-size': 10});
        label.textContent = format(ceiling * tick / 4);
        svg.append(label);
      }
      const times = series.flat().map(s => Date.parse(s.time));
      const first = Math.min(...times), last = Math.max(...times);
      series.forEach((values, i) => {
        let segment = [];
        const flush = () => {
          if (!segment.length) return;
          const firstX = segment[0].split(',')[0], lastX = segment[segment.length - 1].split(',')[0];
          svg.append(svgNode('polygon', {points: `${firstX},155 ${segment.join(' ')} ${lastX},155`, fill: colors[i], 'fill-opacity': '.08'}));
          const line = document.createElementNS(ns, 'polyline');
          line.setAttribute('points', segment.join(' '));
          line.setAttribute('fill', 'none');
          line.setAttribute('stroke', colors[i]);
          line.setAttribute('stroke-width', '2.5');
          line.setAttribute('class', 'chart-series-line');
          line.setAttribute('stroke-linecap', 'round');
          line.setAttribute('stroke-linejoin', 'round');
          line.setAttribute('vector-effect', 'non-scaling-stroke');
          svg.append(line);
          segment = [];
        };
        values.forEach((sample, j) => {
          const time = Date.parse(sample.time);
          if (j && time - Date.parse(values[j - 1].time) > 30000) flush();
          if (!Number.isFinite(sample.value)) { flush(); return; }
          const x = 80 + (time - first) * (width - 90) / Math.max(1, last - first);
          const y = 155 - sample.value / ceiling * 140;
          segment.push(`${x},${y}`);
          const dot = svgNode('circle', {cx: x, cy: y, r: 2, fill: colors[i], class: 'chart-point'});
          const tooltip = svgNode('title', {});
          tooltip.textContent = `${new Date(sample.time).toLocaleTimeString()}: ${format(sample.value)}`;
          dot.append(tooltip); svg.append(dot);
        });
        flush();
      });
      return svg;
    }
    function resource(title, value, detail, series, colors, percentage) {
      const node = el('div', undefined, 'resource');
      node.append(el('h3', title), el('strong', value), el('small', detail));
      const format = percentage !== undefined ? n => `${n.toFixed(0)}%` : title === 'NGINX log storage' ? bytes : rate;
      node.append(chart(series, colors, percentage !== undefined, format, title));
      const legend = el('div', undefined, 'chart-series-legend');
      colors.forEach((color, index) => {
        const label = el('span', colors.length > 1 ? (index === 0 ? 'Receive' : 'Send') : title);
        label.setAttribute('style', `--series-color: ${color}`);
        legend.append(label);
      });
      node.append(legend);
      const points = series.flat();
      if (points.length) {
        const formatTime = time => new Date(time).toLocaleTimeString();
        const axis = el('div', undefined, 'graph-time-axis');
        axis.append(el('span', formatTime(points[0].time)), el('span', formatTime(points[points.length - 1].time)));
        node.append(axis);
      }
      if (percentage !== undefined) {
        const meter = el('meter');
        meter.min = 0; meter.max = 100; meter.value = percentage;
        meter.setAttribute('aria-label', `${title} ${percentage.toFixed(1)} percent`);
        node.append(meter);
      }
      return node;
    }
    function render(servers) {
      const nextCards = [];
      if (!servers.length) nextCards.push(el('p', 'NGINX host monitoring is not configured. Enroll the reverse proxy server and start its monitoring agent.', 'panel monitor-empty'));
      servers.forEach(server => {
        const card = el('section', undefined, `panel server-card ${server.status}`);
        const heading = el('div', undefined, 'server-title');
        heading.append(el('h2', server.name), el('span', labels[server.status], `server-state ${server.status}`));
        card.append(heading, el('p', server.address + ' \u00b7 Host-wide resource usage', 'server-subtitle'));
        nextCards.push(card);
        const m = server.metrics;
        if (!m) {
          card.append(el('p', 'Resource measurements will appear when this server’s monitoring agent connects.', 'monitor-empty'));
          return;
        }
        card.append(el('p', `Last received: ${new Date(server.received_at).toLocaleString()}`, 'server-subtitle'));
        const saved = server.history || [];
        const seconds = Number(range?.value ?? 180);
        const end = Date.parse(server.received_at);
        const samples = seconds > 0 ? saved.filter(s => Date.parse(s.time) >= end - seconds * 1000) : saved;
        const nginxDisks = m.disks.filter(d => d.kind === 'directory' && d.mount.replace(/\/+$/, '') === '/var/log/nginx');
        const disk = nginxDisks.length ? nginxDisks[0].used : null;
        const rx = Number.isFinite(m.nginx_rx_bps) ? m.nginx_rx_bps : null;
        const tx = Number.isFinite(m.nginx_tx_bps) ? m.nginx_tx_bps : null;
        const values = key => samples.map(s => ({time: s.time, value: s[key]}));
        const grid = el('div', undefined, 'resource-grid');
        grid.append(resource('CPU', `${m.cpu.toFixed(1)}%`, 'Total processor utilization', [values('cpu')], ['#0875df'], m.cpu));
        grid.append(resource('RAM', `${m.ram.percent.toFixed(1)}%`, `${bytes(m.ram.used)} / ${bytes(m.ram.total)}`, [values('ram')], ['#15986a'], m.ram.percent));
        grid.append(resource('NGINX log storage', nginxDisks.length ? bytes(disk) : 'Unavailable', 'File size inside /var/log/nginx/', [values('disk')], ['#d68b00']));
        grid.append(resource('External NGINX Traffic', rx === null ? 'Unavailable' : `Receive: ${rate(rx)}`, tx === null ? 'Update the agent and check access log permissions.' : `Send: ${rate(tx)}; receive (blue), send (purple)`, [values('rx'), values('tx')], ['#0875df', '#8b5cf6']));
        card.append(grid);
        card.append(el('p', 'External traffic uses completed requests from managed website access logs, including HTTP headers. Long-running requests appear when logged; TLS and network overhead are excluded.', 'server-subtitle'));
        const devices = el('div', undefined, 'server-devices');
        const disks = el('div'); disks.append(el('h3', 'NGINX log storage'));
        nginxDisks.forEach(d => {
          const row = el('div', undefined, 'device-row');
          row.append(el('span', d.mount), el('span', `${bytes(d.used)} of log files`)); disks.append(row);
        });
        if (!nginxDisks.length) disks.append(el('p', 'Storage data unavailable. Update the agent and check access to /var/log/nginx/.', 'server-subtitle'));
        disks.append(el('p', 'Total file size including subdirectories and rotated logs. Symbolic links are excluded; hard-linked files are counted once.', 'server-subtitle'));
        const network = el('div'); network.append(el('h3', 'NIC diagnostics (host traffic)'));
        m.interfaces.forEach(n => {
          const utilization = n.speed_mbps > 0 ? `${(Math.max(n.rx, n.tx) * 8 / (n.speed_mbps * 1e6) * 100).toFixed(1)}% of ${n.speed_mbps} Mbps link` : 'Link capacity unknown';
          const row = el('div', undefined, 'device-row');
          const state = n.is_up === true ? 'Up' : n.is_up === false ? 'Down' : 'Link state unknown';
          row.append(el('span', `${n.name} \u00b7 ${state}`), el('span', n.rate_available === false ? 'Waiting for rate sample' : `RX ${bytes(n.rx)}/s · TX ${bytes(n.tx)}/s · ${utilization}`)); network.append(row);
        });
        if (!m.interfaces.length) network.append(el('p', 'No non-loopback interface measurements reported.', 'server-subtitle'));
        network.append(el('p', 'Host interface counters include all processes and are separate from External NGINX Traffic. Virtual interfaces may duplicate traffic. Link speed is reported by the operating system.', 'server-subtitle'));
        devices.append(disks, network); card.append(devices);
      });
      // Build every chart before touching the live DOM. Reading chart width
      // while the page is empty forces layout and clamps scroll to the top.
      updateView(() => cards.replaceChildren(...nextCards));
    }
    async function poll() {
      if (stopped) return;
      if (document.hidden) { timer = window.setTimeout(poll, 3000); return; }
      try {
        const response = await fetch(root.dataset.url, {cache: 'no-store', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)])});
        if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error();
        const data = await response.json();
        if (stopped) return;
        latestServers = data.servers;
        render(latestServers);
        status.textContent = `Updated ${new Date().toLocaleTimeString()}`;
      } catch {
        if (stopped) return;
        status.textContent = 'Updates unavailable. Check your connection or sign in again.';
        cards.querySelectorAll('.server-card').forEach(card => {
          card.classList.add('stale');
          const badge = card.querySelector('.server-state');
          badge.classList.remove('live'); badge.textContent = 'Updates unavailable';
        });
      } finally { if (!stopped) timer = window.setTimeout(poll, 3000); }
    }
    poll();
  }
  document.addEventListener('app:before-render', () => cleanup());
  document.addEventListener('app:navigate', start);
  start();
})();
