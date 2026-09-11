(() => {
  const rows = document.getElementById('traffic-rows');
  const status = document.getElementById('traffic-refresh-status');
  let busy = false;
  async function refresh() {
    if (busy) return;
    busy = true;
    try {
      const response = await fetch(rows.dataset.url, {cache: 'no-store', signal: AbortSignal.timeout(8000)});
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error();
      const data = await response.json();
      if (typeof data.html !== 'string') throw new Error();
      // The authenticated endpoint renders these rows with Django HTML escaping.
      rows.innerHTML = data.html;
      status.textContent = `Updated ${new Date().toLocaleTimeString()} · Auto-refresh every 5 seconds`;
    } catch {
      status.textContent = 'Refresh failed. Showing previous logs; retrying automatically. Check your connection or sign in again.';
    } finally { busy = false; }
  }
  window.setInterval(refresh, 5000);
})();
