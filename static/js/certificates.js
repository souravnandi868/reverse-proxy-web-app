document.querySelectorAll('form[data-delete-certificate]').forEach(form => {
  form.addEventListener('submit', event => {
    const name = form.dataset.deleteCertificate;
    if (!window.confirm(`Delete certificate bundle "${name}" from the inventory? This cannot be undone. Certificates assigned to proxies cannot be deleted. Stored PEM files will be retained for existing configurations and backups.`)) {
      event.preventDefault();
    }
  });
  form.querySelector('button').disabled = false;
});
