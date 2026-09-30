(() => {
  let cleanup = () => {};
  const node = (tag, text, cls) => {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    if (cls) result.className = cls;
    return result;
  };
  const size = value => {
    if (value === null) return 'Unavailable';
    const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    let i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
    return `${value.toFixed(1)} ${units[i]}`;
  };
  const ms = value => value === null ? 'Unavailable' : `${value.toFixed(1)} ms`;
  function chart(title, value, detail, samples, keys, colors, format) {
    const card = node('section', undefined, 'panel usage-chart');
    card.append(node('h3', title), node('strong', value), node('p', detail, 'usage-legend'));
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 600 170');
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', title);
    const ceiling = Math.max(1, ...samples.flatMap(s => keys.map(key => s[key])).filter(Number.isFinite));
    keys.forEach((key, index) => {
      let points = [];
      const flush = () => {
        if (!points.length) return;
        const line = document.createElementNS(ns, 'polyline');
        line.setAttribute('points', points.join(' '));
        line.setAttribute('fill', 'none');
        line.setAttribute('stroke', colors[index]);
        line.setAttribute('stroke-width', '2');
        svg.append(line); points = [];
      };
      samples.forEach((sample, i) => {
        if (!Number.isFinite(sample[key])) { flush(); return; }
        const x = 5 + 590 * i / Math.max(1, samples.length - 1);
        const y = 160 - sample[key] / ceiling * 150;
        points.push(`${x},${y}`);
        const dot = document.createElementNS(ns, 'circle');
        dot.setAttribute('cx', x); dot.setAttribute('cy', y); dot.setAttribute('r', '2');
        dot.setAttribute('fill', colors[index]);
        const tooltip = document.createElementNS(ns, 'title');
        tooltip.textContent = `${new Date(sample.time).toLocaleTimeString()}: ${format(sample[key])}`;
        dot.append(tooltip); svg.append(dot);
      });
      flush();
    });
    card.append(svg);
    if (samples.length) {
      const axis = node('div', undefined, 'usage-axis');
      axis.append(node('span', new Date(samples[0].time).toLocaleTimeString()),
        node('span', `Scale: 0 – ${format(ceiling)}`),
        node('span', new Date(samples[samples.length - 1].time).toLocaleTimeString()));
      card.append(axis);
    }
    return card;
  }
  function start() {
    cleanup();
    const root = document.getElementById('website-usage');
    if (!root) return;
    const fqdn = document.getElementById('usage-fqdn');
    const range = document.getElementById('usage-range');
    const charts = document.getElementById('usage-charts');
    const sites = document.getElementById('usage-sites');
    const status = document.getElementById('usage-status');
    let stopped = false, timer, controller, generation = 0;
    function render(data) {
      const t = data.total, s = data.series;
      charts.replaceChildren(
        chart('Traffic', `RX ${size(t.rx)} · TX ${size(t.tx)}`, 'Receive (blue), send (purple); average bytes/sec per interval.', s,
          ['rx_bps', 'tx_bps'], ['#0875df', '#8b5cf6'], n => `${size(n)}/s`),
        chart('Requests', `${t.requests} requests`, 'Completed requests per interval.', s, ['requests'], ['#0875df'], n => String(n)),
        chart('Errors', `${t.errors} errors`, 'HTTP 4xx and 5xx responses per interval.', s, ['errors'], ['#dc5353'], n => String(n)),
        chart('Response time', ms(t.response_ms), 'Average NGINX request duration; includes client transfer time.', s, ['response_ms'], ['#15986a'], ms));
      sites.replaceChildren();
      const max = Math.max(1, ...data.sites.map(site => site.requests));
      for (const site of data.sites) {
        const row = node('tr');
        const name = node('td', site.fqdn);
        name.append(node('small', site.backend));
        const requests = node('td', String(site.requests));
        const bar = node('meter'); bar.min = 0; bar.max = max; bar.value = site.requests;
        bar.setAttribute('aria-label', `${site.requests} requests for ${site.fqdn}`);
        requests.append(bar);
        row.append(name, requests, node('td', size(site.rx)), node('td', size(site.tx)),
          node('td', site.error_percent === null ? 'Unavailable' : `${site.errors} (${site.error_percent.toFixed(1)}%)`),
          node('td', ms(site.response_ms)));
        sites.append(row);
      }
      if (!data.sites.length) {
        const row = node('tr'), cell = node('td', 'No collected requests in this time range. Check that the traffic collector is running.');
        cell.setAttribute('colspan', '6'); row.append(cell); sites.append(row);
      }
    }
    async function poll() {
      if (stopped) return;
      if (document.hidden) { timer = setTimeout(poll, 3000); return; }
      const current = ++generation;
      controller = new AbortController();
      try {
        const query = new URLSearchParams({fqdn: fqdn.value, seconds: range.value});
        const response = await fetch(`${root.dataset.url}?${query}`, {cache: 'no-store',
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)])});
        if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error();
        const data = await response.json();
        if (stopped || current !== generation) return;
        render(data);
        charts.classList.remove('usage-stale');
        status.textContent = `Updated ${new Date(data.end).toLocaleTimeString()}`;
      } catch {
        if (stopped || current !== generation) return;
        charts.classList.add('usage-stale');
        status.textContent = 'Usage updates unavailable. Check your connection or sign in again.';
      } finally {
        if (!stopped && current === generation) timer = setTimeout(poll, 3000);
      }
    }
    const change = () => {
      clearTimeout(timer); generation++; controller?.abort();
      charts.replaceChildren(); sites.replaceChildren(); status.textContent = 'Loading usage…';
      poll();
    };
    fqdn.addEventListener('change', change); range.addEventListener('change', change);
    cleanup = () => {
      stopped = true; generation++; clearTimeout(timer); controller?.abort();
      fqdn.removeEventListener('change', change); range.removeEventListener('change', change);
    };
    poll();
  }
  document.addEventListener('app:before-render', () => cleanup());
  document.addEventListener('app:navigate', start);
  start();
})();
