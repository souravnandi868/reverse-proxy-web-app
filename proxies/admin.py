from django.contrib import admin
from .models import AuditLog, CertificateBundle, ConfigurationBackup, ProxyConfig

admin.site.register([ProxyConfig, CertificateBundle, ConfigurationBackup, AuditLog])
