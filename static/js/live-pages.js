(() => {
  let cleanup = () => {};
  let pointerDown = false;
  document.addEventListener('pointerdown', () => { pointerDown = true; });
  document.addEventListener('pointerup', () => { pointerDown = false; });
  document.addEventListener('pointercancel', () => { pointerDown = false; });
  window.addEventListener('blur', () => { pointerDown = false; });
  function start() {
    cleanup();
    const regions = [...document.querySelectorAll('[data-live-region]')];
    if (!regions.length) return;
    const status = document.createElement('p');
    status.className = 'muted';
    status.setAttribute('role', 'status');
    status.textContent = '';
    status.hidden = true;
    document.querySelector('.topbar').after(status);
    let busy = false;
    let stopped = false;
    const controller = new AbortController();
    let timer;
    const onVisible = () => { if (!document.hidden) refresh(); };
    cleanup = () => {
      stopped = true;
      controller.abort();
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
      status.remove();
    };

    async function refresh() {
      if (busy || stopped || window.appBusy || document.hidden || pointerDown) return;
      busy = true;
      try {
        const response = await fetch(window.location.href, {
          cache: 'no-store', credentials: 'same-origin',
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]),
        });
        if (!response.ok || response.redirected || !response.headers.get('content-type')?.includes('text/html')) {
          throw new Error('Refresh unavailable');
        }
        const page = new DOMParser().parseFromString(await response.text(), 'text/html');
        const replacements = regions.map(region => page.querySelector(`[data-live-region="${region.dataset.liveRegion}"]`));
        if (replacements.some(region => !region)) throw new Error('Missing content');
        if (stopped || window.appBusy || document.hidden || pointerDown) return;
        regions.forEach((region, index) => {
          // Keep keyboard focus and any ongoing interaction in place.
          if (region.contains(document.activeElement)) return;
          const scrollPositions = [...region.querySelectorAll('.table-wrap')].map(el => el.scrollLeft);
          region.innerHTML = replacements[index].innerHTML;
          region.querySelectorAll('.table-wrap').forEach((el, i) => { el.scrollLeft = scrollPositions[i] || 0; });
        });
        document.dispatchEvent(new Event('live-content-updated'));
        status.textContent = '';
        status.hidden = true;
      } catch {
        if (stopped) return;
        status.textContent = 'Update failed. Showing previous data; retrying automatically. Check your connection or sign in again.';
        status.hidden = false;
      } finally {
        busy = false;
      }
    }
    timer = window.setInterval(refresh, 5000);
    document.addEventListener('visibilitychange', onVisible);
  }
  document.addEventListener('app:before-render', () => cleanup());
  document.addEventListener('app:navigate', start);
  start();
})();
