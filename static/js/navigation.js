// Keep the document alive while Django renders authenticated views and validates forms.
(() => {
  let requestId = 0;
  let controller;
  let posting = false;
  let pendingHistory = null;
  const appPath = path => path === '/' || /^\/(proxies|domains|servers|certificates|audit|login|logout)\//.test(path);

  function notice(message, error = false) {
    let node = document.getElementById('navigation-status');
    if (!node) {
      node = document.createElement('div');
      node.id = 'navigation-status';
      (document.querySelector('main, .login-panel') || document.body).prepend(node);
    }
    node.className = error ? 'message error' : 'muted';
    node.setAttribute('role', error ? 'alert' : 'status');
    node.textContent = message;
  }

  function render(page) {
    if (!page.querySelector('main.main, .login-panel')) throw new Error('Unexpected response');
    document.dispatchEvent(new Event('app:before-render'));
    for (const sheet of page.querySelectorAll('link[rel="stylesheet"]')) {
      if (![...document.querySelectorAll('link[rel="stylesheet"]')].some(existing => existing.href === sheet.href)) {
        document.head.append(sheet.cloneNode(true));
      }
    }
    // Controllers are loaded once and restarted through lifecycle events.
    page.querySelectorAll('script').forEach(script => script.remove());
    const currentMain = document.querySelector('main.main');
    const nextMain = page.querySelector('main.main');
    if (currentMain && nextMain) {
      currentMain.replaceWith(nextMain);
      document.querySelector('.sidebar')?.replaceWith(page.querySelector('.sidebar'));
    } else {
      document.body.replaceChildren(...page.body.childNodes);
    }
    document.body.className = page.body.className;
    document.title = page.title;
    document.dispatchEvent(new Event('app:navigate'));
    const heading = document.querySelector('h1');
    if (heading) {
      heading.setAttribute('tabindex', '-1');
      heading.focus({preventScroll: true});
    }
  }

  async function visit(url, {body, method = 'GET', historyMode = 'push'} = {}) {
    if (posting) return;
    controller?.abort();
    controller = new AbortController();
    const id = ++requestId;
    posting = method !== 'GET';
    window.appBusy = true;
    // Stop background reads so they cannot overwrite action results or consume messages.
    document.dispatchEvent(new Event('app:before-render'));
    notice(posting ? 'Saving changes…' : 'Loading…');
    document.body.setAttribute('aria-busy', 'true');
    let rendered = false;
    try {
      const response = await fetch(url, {
        method, body, credentials: 'same-origin', cache: 'no-store',
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(120000)]),
      });
      if (!response.ok) throw new Error(`Request failed (${response.status})`);
      if (!response.headers.get('content-type')?.includes('text/html')) throw new Error('Unexpected response');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (id !== requestId) return;
      const destination = new URL(response.url || url, window.location.href);
      if (destination.origin !== window.location.origin || !appPath(destination.pathname)) throw new Error('Unexpected destination');
      render(page);
      rendered = true;
      if (historyMode === 'none') history.replaceState({}, '', destination);
      else if (destination.href !== window.location.href) history.pushState({}, '', destination);
      window.scrollTo(0, 0);
    } catch (error) {
      if (id !== requestId) return;
      notice(method === 'GET'
        ? 'Could not load this view. Check your connection and try again.'
        : 'Could not confirm the result. Your form is preserved. Check the current entries before submitting again.', true);
    } finally {
      if (id === requestId) {
        posting = false;
        window.appBusy = false;
        document.body.removeAttribute('aria-busy');
        if (!rendered) document.dispatchEvent(new Event('app:navigate'));
        if (pendingHistory) {
          const next = pendingHistory;
          pendingHistory = null;
          visit(next, {historyMode: 'none'});
        }
      }
    }
  }

  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey
      || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin || !appPath(url.pathname) || url.pathname.endsWith('.pdf')
      || (url.hash && url.pathname === window.location.pathname && url.search === window.location.search)) return;
    event.preventDefault();
    if (!posting) visit(url.href);
  });

  // Certificate confirmation runs first; cancelled submissions remain cancelled.
  document.addEventListener('submit', event => {
    if (event.defaultPrevented) return;
    const form = event.target;
    const url = new URL(form.action || window.location.href, window.location.href);
    if (url.origin !== window.location.origin || !appPath(url.pathname)) return;
    event.preventDefault();
    if (posting) return;
    const data = new FormData(form);
    if (event.submitter?.name) data.append(event.submitter.name, event.submitter.value);
    const method = form.method.toUpperCase();
    if (method === 'GET') {
      url.search = new URLSearchParams(data).toString();
      visit(url.href);
    } else {
      visit(url.href, {method, body: data});
    }
  });

  window.addEventListener('popstate', () => {
    if (posting) pendingHistory = window.location.href;
    else visit(window.location.href, {historyMode: 'none'});
  });
})();
