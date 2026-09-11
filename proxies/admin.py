from django.contrib import admin
from .models import AuditLog, CertificateBundle, ConfigurationBackup, ProxyConfig
from .forms import ProxyConfigForm


@admin.register(ProxyConfig)
class ProxyConfigAdmin(admin.ModelAdmin):
    form = ProxyConfigForm
    readonly_fields = ("public_ip",)

    def has_delete_permission(self, request, obj=None):
        # All proxy deletion must pass through the helper-backed confirmation view.
        return False


admin.site.register([CertificateBundle, ConfigurationBackup, AuditLog])
