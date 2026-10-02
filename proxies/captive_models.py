import re
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models, transaction
from django.db.models.functions import Lower
from django.utils import timezone


def normalize_mobile(value):
    value = re.sub(r"[\s()-]", "", value or "")
    if value.startswith("+91"):
        value = value[3:]
    elif len(value) == 12 and value.startswith("91"):
        value = value[2:]
    elif len(value) == 11 and value.startswith("0"):
        value = value[1:]
    if not re.fullmatch(r"[6-9][0-9]{9}", value):
        raise ValidationError("Enter a valid Indian mobile number.")
    return "+91" + value


class CaptivePortalUser(models.Model):
    name = models.CharField(max_length=150)
    section = models.CharField(max_length=120)
    rank = models.CharField(max_length=120)
    mobile_number = models.CharField(max_length=13, null=True, unique=True)
    email_address = models.EmailField(max_length=254, null=True)
    is_enabled = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    all_fqdns = models.BooleanField("Allow access to all captive-enabled FQDNs", default=False)
    proxies = models.ManyToManyField("ProxyConfig", blank=True, related_name="captive_users")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="captive_users_created")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="captive_users_updated")
    last_successful_login = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name", "pk"]
        permissions = [("toggle_captiveportaluser", "Enable or disable authorized users"),
                       ("assign_captiveportaluser", "Assign authorized user FQDN access")]
        constraints = [models.UniqueConstraint(Lower("email_address"), name="captive_unique_email"),
                       models.CheckConstraint(condition=(models.Q(deleted_at__isnull=False) | models.Q(is_enabled=False)
                           | models.Q(mobile_number__isnull=False, email_address__isnull=False)), name="captive_contact_required")]

    def __str__(self):
        return self.name

    def clean(self):
        if not self.deleted_at:
            for field in ("name", "section", "rank"):
                value = (getattr(self, field) or "").strip()
                if not value:
                    raise ValidationError({field: "This field is required."})
                setattr(self, field, value)
        self.mobile_number = normalize_mobile(self.mobile_number) if self.mobile_number else None
        self.email_address = (self.email_address or "").strip().lower() or None
        if self.email_address:
            validate_email(self.email_address)
        if not self.deleted_at and not (self.mobile_number and self.email_address):
            raise ValidationError("Both a mobile number and email address are required.")

    def save(self, *args, **kwargs):
        self.clean()
        with transaction.atomic():
            if self.pk:
                type(self).objects.filter(pk=self.pk).update(updated_at=models.F("updated_at"))
            old = type(self).objects.filter(pk=self.pk).first() if self.pk else None
            super().save(*args, **kwargs)
            if old and (old.mobile_number != self.mobile_number or old.email_address != self.email_address
                        or old.is_enabled != self.is_enabled or old.deleted_at != self.deleted_at
                        or old.all_fqdns != self.all_fqdns):
                self.revoke()

    def revoke(self, proxy_ids=None):
        sessions = self.captive_sessions.filter(revoked=False)
        otps = self.captive_otps.filter(consumed_at__isnull=True)
        if proxy_ids is not None:
            sessions = sessions.filter(proxy_id__in=proxy_ids)
            otps = otps.filter(proxy_id__in=proxy_ids)
        for session in sessions:
            CaptiveAudit.objects.create(user=self, proxy=session.proxy, action="session_revoked", actor=self.updated_by)
        sessions.update(revoked=True)
        otps.update(consumed_at=timezone.now())

    def delete(self, *args, **kwargs):
        self.deleted_at = timezone.now()
        self.is_enabled = False
        self.name = "Deleted user"
        self.section = self.rank = ""
        self.mobile_number = self.email_address = None
        self.save()

    def authorized(self, proxy):
        return (self.is_enabled and not self.deleted_at and proxy.enabled and proxy.captive_portal_enabled
                and (self.all_fqdns or self.proxies.filter(pk=proxy.pk).exists()))


class CaptiveOTP(models.Model):
    user = models.ForeignKey(CaptivePortalUser, on_delete=models.PROTECT, related_name="captive_otps")
    proxy = models.ForeignKey("ProxyConfig", on_delete=models.CASCADE)
    challenge_hash = models.CharField(max_length=64, unique=True)
    channel = models.CharField(max_length=5, choices=[("sms", "SMS")], default="sms")
    destination_fingerprint = models.CharField(max_length=64)
    otp_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    failed_attempts = models.PositiveIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True)
    delivered = models.BooleanField(default=False)
    request_ip = models.GenericIPAddressField(null=True)


class CaptiveSession(models.Model):
    user = models.ForeignKey(CaptivePortalUser, on_delete=models.PROTECT, related_name="captive_sessions")
    proxy = models.ForeignKey("ProxyConfig", on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked = models.BooleanField(default=False)
    last_used_at = models.DateTimeField(default=timezone.now)
    client_ip = models.GenericIPAddressField(null=True)
    user_agent = models.CharField(max_length=512, blank=True)


class CaptiveRateLimit(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    window_start = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)


class CaptiveAudit(models.Model):
    user = models.ForeignKey(CaptivePortalUser, null=True, on_delete=models.SET_NULL)
    proxy = models.ForeignKey("ProxyConfig", null=True, on_delete=models.SET_NULL)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=40, db_index=True)
    channel = models.CharField(max_length=5, blank=True)
    client_ip = models.GenericIPAddressField(null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]


from django.db.models.signals import m2m_changed
from django.dispatch import receiver


@receiver(m2m_changed, sender=CaptivePortalUser.proxies.through)
def assignments_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in {"pre_remove", "pre_clear", "post_add"}:
        return
    users = CaptivePortalUser.objects.filter(pk__in=pk_set) if reverse and pk_set is not None else (
        instance.captive_users.all() if reverse else [instance])
    for user in users:
        CaptivePortalUser.objects.filter(pk=user.pk).update(updated_at=models.F("updated_at"))
        ids = {instance.pk} if reverse else (pk_set if pk_set is not None else set(user.proxies.values_list("pk", flat=True)))
        if action != "post_add" and not user.all_fqdns:
            user.revoke(ids)
        for proxy_id in ids:
            CaptiveAudit.objects.create(user=user, proxy_id=proxy_id, actor=user.updated_by,
                                       action="fqdn_assigned" if action == "post_add" else "fqdn_removed")
