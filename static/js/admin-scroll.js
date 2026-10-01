// Native Django admin submissions retain their scroll position too.
(() => {
  const key = 'proxy-admin-operation-position';
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
    sessionStorage.removeItem(key);
    if (saved && Date.now() - saved.time < 120000 && !location.pathname.includes('/login/')) {
      window.scrollTo(saved.left, saved.top);
      window.addEventListener('load', () => window.scrollTo(saved.left, saved.top), {once: true});
    }
  } catch { /* Storage can be unavailable in restricted browser sessions. */ }
  document.addEventListener('submit', event => {
    if (event.defaultPrevented) return;
    const destination = new URL(event.target.action || location.href, location.href);
    if (destination.origin !== location.origin || !destination.pathname.startsWith('/admin/')
        || /\/(login|logout)\//.test(destination.pathname)) return;
    try {
      sessionStorage.setItem(key, JSON.stringify({left: scrollX, top: scrollY, time: Date.now()}));
    } catch { /* Submission still works without session storage. */ }
  });
})();
