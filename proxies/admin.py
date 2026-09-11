from django.contrib import admin
from .models import AuditLog, CertificateBundle, ConfigurationBackup, ProxyConfig
from .forms import ProxyConfigForm


@admin.register(ProxyConfig)
class ProxyConfigAdmin(admin.ModelAdmin):
    form = ProxyConfigForm
    readonly_fields = ("public_ip",)


admin.site.register([CertificateBundle, ConfigurationBackup, AuditLog])
