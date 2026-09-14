(() => {
  function closeMenus(restoreFocus = false) {
    document.querySelectorAll('.account-dropdown[open]').forEach(menu => {
      menu.open = false;
      if (restoreFocus) menu.querySelector('summary').focus();
    });
  }

  document.addEventListener('click', event => {
    if (!event.target.closest('.account-dropdown')) closeMenus();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeMenus(true);
  });
  document.addEventListener('app:before-render', () => closeMenus());

  function focusContactField() {
    // Navigation updates the URL after rendering the new page.
    queueMicrotask(() => {
      if (['#id_mobile_number', '#id_email'].includes(window.location.hash)) {
        document.querySelector(window.location.hash)?.focus();
      }
    });
  }
  document.addEventListener('app:navigate', focusContactField);
  focusContactField();
})();
