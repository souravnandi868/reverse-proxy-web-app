// Runs before paint; all preferences and assets stay on this installation.
(() => {
  const root = document.documentElement;
  let theme = 'dark';
  try { theme = localStorage.getItem('console-color-theme') || theme; } catch {}
  if (!['dark', 'light'].includes(theme)) theme = 'dark';
  root.dataset.consoleTheme = theme;
  function label() {
    document.querySelectorAll('.theme-toggle').forEach(button => {
      const next = root.dataset.consoleTheme === 'dark' ? 'light' : 'dark';
      button.setAttribute('aria-label', `Switch to ${next} theme`);
      button.title = `Switch to ${next} theme`;
    });
  }
  document.addEventListener('click', event => {
    if (!event.target.closest('.theme-toggle')) return;
    theme = root.dataset.consoleTheme === 'dark' ? 'light' : 'dark';
    root.dataset.consoleTheme = theme;
    try { localStorage.setItem('console-color-theme', theme); } catch {}
    label();
  });
  function ready() {
    label();
    const main = document.querySelector('main.main');
    if (main) {
      main.classList.remove('view-enter');
      requestAnimationFrame(() => main.classList.add('view-enter'));
    }
  }
  document.addEventListener('DOMContentLoaded', ready);
  document.addEventListener('app:navigate', ready);
})();
