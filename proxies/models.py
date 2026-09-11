from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class CertificateBundle(models.Model):
    name = models.CharField(max_length=120, unique=True)
    certificate = models.FileField(upload_to="certificates/", max_length=255)
    private_key = models.FileField(upload_to="keys/", max_length=255)
    valid_until = models.DateField(null=True, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return self.name


class ProxyConfig(models.Model):
    PROTOCOLS = [("http", "HTTP"), ("https", "HTTPS")]
    domain_name = models.CharField(max_length=253, unique=True)
    public_ip = models.GenericIPAddressField(protocol="both", null=True, blank=True)
    backend_private_ip = models.GenericIPAddressField(protocol="both")
    backend_port = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(65535)])
    incoming_protocol = models.CharField(max_length=5, choices=PROTOCOLS, default="https")
    backend_protocol = models.CharField(max_length=5, choices=PROTOCOLS, default="http")
    certificate_bundle = models.ForeignKey(CertificateBundle, null=True, blank=True, on_delete=models.PROTECT)
    enabled = models.BooleanField(default=True)
    nat_notes = models.CharField(max_length=500, blank=True)
    firewall_notes = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="proxy_configs_created")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="proxy_configs_updated")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["domain_name"]

    def __str__(self):
        return self.domain_name


class ConfigurationBackup(models.Model):
    proxy = models.ForeignKey(ProxyConfig, on_delete=models.CASCADE, related_name="backups")
    version = models.PositiveIntegerField()
    rendered_config = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=120)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["proxy", "version"], name="unique_proxy_backup_version")]


class AuditLog(models.Model):
    ACTIONS = [("create", "Created"), ("update", "Updated"), ("enable", "Enabled"), ("disable", "Disabled"), ("delete", "Deleted"), ("apply", "Applied"), ("rollback", "Rolled back"), ("certificate", "Certificate uploaded"), ("connectivity", "Connectivity tested")]
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=20, choices=ACTIONS)
    target = models.CharField(max_length=253)
    detail = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class ServerMonitor(models.Model):
    address = models.GenericIPAddressField(unique=True)
    token_hash = models.CharField(max_length=64)
    latest = models.JSONField(default=dict)
    received_at = models.DateTimeField(null=True, blank=True)
