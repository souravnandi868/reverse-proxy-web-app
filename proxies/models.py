from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


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

    @property
    def expiry(self):
        if self.valid_until is None:
            return {"state": "unknown", "label": "Expiry date not recorded"}
        days = (self.valid_until - timezone.localdate()).days
        state = "critical" if days < 15 else "warning" if days <= 30 else "healthy"
        if days < 0:
            count = abs(days)
            label = f"Expired {count} day{'s' if count != 1 else ''} ago"
        elif days == 0:
            label = "Expires today"
        else:
            label = f"{days} day{'s' if days != 1 else ''} remaining"
        return {"state": state, "label": label}


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
    websocket_enabled = models.BooleanField("Enable WebSocket support", default=False)
    captive_portal_enabled = models.BooleanField("Enable OTP captive portal", default=False)
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
    is_reverse_proxy = models.BooleanField(default=False)
    token_hash = models.CharField(max_length=64)
    latest = models.JSONField(default=dict)
    history = models.JSONField(default=list)
    received_at = models.DateTimeField(null=True, blank=True)


class AccountProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="account_profile")
    mobile_number = models.CharField(max_length=25, blank=True)


class TrafficCursor(models.Model):
    """Durable position; identity survives a log being renamed during rotation."""
    identity = models.CharField(max_length=100, unique=True)
    offset = models.PositiveBigIntegerField(default=0)
    anchor = models.BinaryField(default=bytes)
    skipping = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


class TrafficEvent(models.Model):
    occurred_at = models.DateTimeField(null=True, blank=True, db_index=True)
    domain = models.CharField(max_length=253, db_index=True)
    data = models.JSONField()

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["domain", "-id"], name="traffic_domain_id"),
                   models.Index(fields=["domain", "occurred_at"], name="traffic_domain_time")]

from .captive_models import CaptivePortalUser, CaptiveOTP, CaptiveSession, CaptiveRateLimit, CaptiveAudit
