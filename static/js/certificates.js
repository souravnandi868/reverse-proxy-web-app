document.addEventListener('submit', event => {
    const form = event.target.closest('form[data-delete-certificate]');
    if (!form) return;
    const name = form.dataset.deleteCertificate;
    if (!window.confirm(`Delete certificate bundle "${name}" from the inventory? This cannot be undone. Certificates assigned to proxies cannot be deleted. Stored PEM files will be retained for existing configurations and backups.`)) {
      event.preventDefault();
    }
});
function enableCertificateDeletion() {
  document.querySelectorAll('form[data-delete-certificate] button').forEach(button => { button.disabled = false; });
}
enableCertificateDeletion();
document.addEventListener('live-content-updated', enableCertificateDeletion);

document.addEventListener('app:navigate', enableCertificateDeletion);
