(() => {
  let cleanup = () => {};
  function start() {
    cleanup();
    const rows = document.getElementById('traffic-rows');
    const status = document.getElementById('traffic-refresh-status');
    if (!rows) return;
    let stopped = false;
    let timer;
    const controller = new AbortController();
    cleanup = () => { stopped = true; clearTimeout(timer); clearInterval(timer); controller.abort(); };
    let busy = false;
    async function refresh() {
      if (busy || stopped || document.hidden) return;
      busy = true;
      try {
        const response = await fetch(rows.dataset.url, {cache: 'no-store', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)])});
        if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error();
        const data = await response.json();
        if (stopped) return;
        if (typeof data.html !== 'string') throw new Error();
        // The authenticated endpoint renders these rows with Django HTML escaping.
        rows.innerHTML = data.html;
        status.textContent = '';
        status.hidden = true;
      } catch {
        if (stopped) return;
        status.textContent = 'Refresh failed. Showing previous logs; retrying automatically. Check your connection or sign in again.';
        status.hidden = false;
      } finally { busy = false; }
    }
    timer = window.setInterval(refresh, 5000);
    refresh();
  }
  document.addEventListener('app:before-render', () => cleanup());
  document.addEventListener('app:navigate', start);
  start();
})();
