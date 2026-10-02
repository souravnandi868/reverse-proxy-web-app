from django.conf import settings
from django.core.checks import Error, register


@register(deploy=True)
def authentication_deployment_checks(app_configs, **kwargs):
    errors = []
    if settings.SECRET_KEY == "dev-only-change-me":
        errors.append(Error("Set a private DJANGO_SECRET_KEY before deployment.", id="proxies.E001"))
    for name in ("AUTH_CAPTCHA_IP_LIMIT", "AUTH_CAPTCHA_SESSION_LIMIT", "AUTH_POST_IP_LIMIT",
                 "AUTH_POST_SESSION_LIMIT", "AUTH_FINGERPRINT_LIMIT", "AUTH_USERNAME_LIMIT",
                 "AUTH_RATE_WINDOW_SECONDS", "AUTH_SESSION_IDLE_SECONDS", "CAPTIVE_MAX_ATTEMPTS",
                 "CAPTIVE_RESEND_SECONDS", "CAPTIVE_SESSION_SECONDS", "CAPTIVE_OTP_EXPIRY_SECONDS"):
        if getattr(settings, name) <= 0:
            errors.append(Error(name + " must be positive.", id="proxies.E002"))
    return errors
