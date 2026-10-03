import ipaddress
import posixpath
import secrets
from datetime import timedelta
from urllib.parse import unquote, urlencode
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_POST
from proxyadmin.captcha import new_captcha, validate_captcha
from .models import ProxyConfig
from .captive_models import CaptivePortalUser, CaptiveOTP, CaptiveSession, CaptiveRateLimit, CaptiveAudit, normalize_mobile
from .captive_sms import deliver

COOKIE = "__Host-captive_session"
CAPTIVE_CAPTCHA_LAST_KEY = "captive_captcha_last"
CAPTIVE_CAPTCHA_PREFIX = "captive_captcha:"
GENERIC = "If the information is registered and authorized, an OTP has been sent to the registered mobile number."


def access_denied(request, user=None, identifier=""):
    event(request, "access_denied", user, "sms")
    return page(request, identifier=identifier,
                error=f"You are not authorized to access {request.captive_proxy.domain_name}. Please contact your administrator.")


def digest(value, purpose="token"):
    return salted_hmac("captive." + purpose, value, algorithm="sha256").hexdigest()


def route_key(domain):
    return digest(domain, "nginx-route")


def local_next(value):
    value = value or "/"
    if len(value) > 4096:
        return "/"
    decoded = value
    for _ in range(5):
        if (not decoded.startswith("/") or decoded.startswith("//") or "\\" in decoded
                or any(ord(c) < 32 or ord(c) == 127 for c in decoded)
                or posixpath.normpath(decoded.split("?", 1)[0]).lower().startswith("/_captive")):
            return "/"
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            return value
        decoded = next_decoded
    return "/"


class CaptiveIngressMiddleware:
    """Only trusted NGINX ingress can use public portal or internal auth views.

    Place before SecurityMiddleware. Never trust X-Forwarded-* from arbitrary clients.
    All captive hosts must also be explicitly listed in DJANGO_ALLOWED_HOSTS.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/_captive"):
            domain = request.get_host().split(":")[0].lower()
            if not constant_time_compare(request.headers.get("X-Captive-Key", ""), route_key(domain)):
                return HttpResponse(status=404)
            proxy = ProxyConfig.objects.filter(domain_name=domain, enabled=True, captive_portal_enabled=True,
                                               incoming_protocol="https").first()
            if not proxy:
                return HttpResponse(status=404)
            request.captive_proxy = proxy
            request.META["HTTPS"] = "on"
            request.META["wsgi.url_scheme"] = "https"
            try:
                request.captive_ip = str(ipaddress.ip_address(request.headers.get("X-Captive-IP", "")))
            except ValueError:
                return HttpResponse(status=400)
        response = self.get_response(request)
        if request.path.startswith("/_captive"):
            response["Cache-Control"] = "no-store, private"
            response["Referrer-Policy"] = "same-origin"
            for name in (settings.CSRF_COOKIE_NAME, settings.SESSION_COOKIE_NAME):
                if name in response.cookies:
                    response.cookies[name]["secure"] = True
        return response


def event(request, action, user=None, channel=""):
    CaptiveAudit.objects.create(action=action, user=user, proxy=request.captive_proxy,
                               channel=channel, client_ip=request.captive_ip)


def limit(key, maximum, seconds):
    """Atomic database increment, including on SQLite with multiple Gunicorn workers.

    Call outside a surrounding transaction: the first operation must acquire a write
    lock before reading. Conditional updates prevent lost increments on all backends.
    """
    now = timezone.now()
    key = digest(key, "rate")
    CaptiveRateLimit.objects.get_or_create(key=key, defaults={"window_start": now})
    with transaction.atomic():
        CaptiveRateLimit.objects.filter(key=key, window_start__lte=now - timedelta(seconds=seconds)).update(window_start=now, count=0)
        return bool(CaptiveRateLimit.objects.filter(key=key, count__lt=maximum).update(count=F("count") + 1))


@sensitive_variables()
def current_session(request):
    token = request.COOKIES.get(COOKIE, "")
    if len(token) != 43:
        return None
    session = CaptiveSession.objects.select_related("user").filter(
        token_hash=digest(token), proxy=request.captive_proxy, revoked=False, expires_at__gt=timezone.now()).first()
    if session:
        from .security import session_fingerprint
        mismatch = (not constant_time_compare(session.client_fingerprint, session_fingerprint(request))
                    or (settings.CAPTIVE_BIND_SESSION_IP and session.client_ip != request.captive_ip)
                    or session.last_used_at <= timezone.now() - timedelta(seconds=settings.AUTH_SESSION_IDLE_SECONDS))
        if mismatch:
            CaptiveSession.objects.filter(pk=session.pk).update(revoked=True)
            event(request, "session_binding_failed", session.user)
            return None
    if session and session.user.authorized(request.captive_proxy):
        CaptiveSession.objects.filter(pk=session.pk).update(last_used_at=timezone.now())
        return session
    return None


@require_GET
@never_cache
def check(request):
    if request.headers.get("X-Captive-Internal") != "1":
        return HttpResponse(status=404)
    return HttpResponse(status=204 if current_session(request) else 401)


@require_GET
@never_cache
def entry(request):
    if request.headers.get("X-Captive-Internal") != "1":
        return HttpResponse(status=404)
    if "application/json" in request.headers.get("Accept", "").lower():
        return JsonResponse({"error": "authentication_required"}, status=401)
    target = local_next(request.headers.get("X-Original-URI", "/"))
    return HttpResponseRedirect("/_captive/login/?" + urlencode({"next": target}))


def page(request, **context):
    captcha_token = secrets.token_urlsafe(32)
    captcha_svg = new_captcha(request, inline=True, session_key=CAPTIVE_CAPTCHA_LAST_KEY)
    # Separate form challenges from console login and other portal tabs. Keep
    # only a bounded number in the session; tokens never contain the answer.
    keys = [key for key in request.session.keys() if key.startswith(CAPTIVE_CAPTCHA_PREFIX)]
    for key in keys[:-7]:
        request.session.pop(key, None)
    request.session[CAPTIVE_CAPTCHA_PREFIX + captcha_token] = {
        **request.session[CAPTIVE_CAPTCHA_LAST_KEY], "proxy_id": request.captive_proxy.pk}
    return render(request, "captive/login.html", {
        "proxy": request.captive_proxy, "next": local_next(request.POST.get("next", request.GET.get("next"))),
        "session": current_session(request), "expiry": settings.CAPTIVE_OTP_EXPIRY_SECONDS,
        "cooldown": settings.CAPTIVE_RESEND_SECONDS,
        "captcha_svg": captcha_svg, "captcha_token": captcha_token,
        "identifier": request.POST.get("identifier", ""), **context})


@require_GET
@never_cache
def login(request):
    return page(request)


@require_POST
@never_cache
@sensitive_post_parameters()
@sensitive_variables()
def send_otp(request):
    from .security import browser_fingerprint
    captcha_token = request.POST.get("captcha_token", "")
    captcha_key = CAPTIVE_CAPTCHA_PREFIX + captcha_token
    challenge = request.session.get(captcha_key, {}) if len(captcha_token) == 43 else {}
    if (challenge.get("proxy_id") != request.captive_proxy.pk
            or not validate_captcha(request, request.POST.get("captcha", ""), session_key=captcha_key)):
        return page(request, error="Enter the image code exactly as shown.")
    identifier = request.POST.get("identifier", "").strip()
    if len(identifier) > 254:
        identifier = ""
    user = None
    is_email = "@" in identifier
    valid = False
    try:
        if is_email:
            identifier = identifier.lower()
            validate_email(identifier)
            user = CaptivePortalUser.objects.filter(email_address__iexact=identifier,
                is_enabled=True, deleted_at__isnull=True).first()
        else:
            identifier = normalize_mobile(identifier)
            user = CaptivePortalUser.objects.filter(mobile_number=identifier,
                is_enabled=True, deleted_at__isnull=True).first()
        valid = True
    except ValidationError:
        pass
    destination = user.mobile_number if user else identifier
    fingerprint = digest(destination, "destination")
    challenge = secrets.token_urlsafe(32)
    event(request, "otp_requested", user, "sms")
    # Evaluate every bucket, including for unknown destinations.
    limits = [("ip:" + request.captive_ip, settings.CAPTIVE_IP_SEND_LIMIT, 3600),
              ("destination:" + fingerprint, settings.CAPTIVE_DESTINATION_SEND_LIMIT, 3600),
              ("cooldown:" + fingerprint, 1, settings.CAPTIVE_RESEND_SECONDS),
              ("fqdn:" + str(request.captive_proxy.pk), settings.CAPTIVE_FQDN_SEND_LIMIT, 3600)]
    if user:
        limits.append(("user:" + str(user.pk), settings.CAPTIVE_USER_SEND_LIMIT, 3600))
    allowed = all([limit(*bucket) for bucket in limits])
    if not allowed:
        event(request, "rate_limited", user, "sms")
    if not valid or not user or not user.authorized(request.captive_proxy):
        return access_denied(request, user, identifier)
    if valid and allowed and user and user.authorized(request.captive_proxy):
        otp = f"{secrets.randbelow(1000000):06d}"
        with transaction.atomic():
            # Acquire a write lock first; serialize issuance against user edits.
            CaptivePortalUser.objects.filter(pk=user.pk).update(updated_at=F("updated_at"))
            user.refresh_from_db()
            identifier_matches = user.email_address == identifier if is_email else user.mobile_number == identifier
            if user.authorized(request.captive_proxy) and identifier_matches and user.mobile_number:
                CaptiveOTP.objects.filter(user=user, proxy=request.captive_proxy, channel="sms",
                                          consumed_at__isnull=True).update(consumed_at=timezone.now())
                record = CaptiveOTP.objects.create(user=user, proxy=request.captive_proxy,
                    channel="sms", challenge_hash=digest(challenge),
                    destination_fingerprint=digest(user.mobile_number, "destination"),
                    otp_hash=digest(challenge + ":" + otp, "otp"),
                    expires_at=timezone.now() + timedelta(seconds=settings.CAPTIVE_OTP_EXPIRY_SECONDS), request_ip=request.captive_ip,
                    client_fingerprint=browser_fingerprint(request))
            else:
                record = None
        if record is None:
            return access_denied(request, user, identifier)
        if record:
            try:
                deliver(user.mobile_number, otp, request.captive_proxy.domain_name)
            except Exception:
                # Never log gateway exceptions: they may embed credentials / payloads.
                CaptiveOTP.objects.filter(pk=record.pk).update(consumed_at=timezone.now())
                event(request, "otp_delivery_failed", user, "sms")
            else:
                CaptiveOTP.objects.filter(pk=record.pk, consumed_at__isnull=True).update(delivered=True)
                event(request, "otp_delivery_accepted", user, "sms")
    return page(request, message=GENERIC, challenge=challenge, identifier=identifier)


@require_POST
@never_cache
@sensitive_post_parameters()
@sensitive_variables()
def verify_otp(request):
    from .security import browser_fingerprint, session_fingerprint
    challenge = request.POST.get("challenge", "")[:128]
    otp = request.POST.get("otp", "")[:20]
    identifier = request.POST.get("identifier", "").strip()
    if len(identifier) > 254:
        identifier = ""
    is_email = "@" in identifier
    identifier_valid = False
    try:
        if is_email:
            identifier = identifier.lower()
            validate_email(identifier)
        else:
            identifier = normalize_mobile(identifier)
        identifier_valid = True
    except ValidationError:
        pass
    token = None
    binding = browser_fingerprint(request)
    if not limit("verify-ip:" + request.captive_ip, settings.CAPTIVE_VERIFY_IP_LIMIT, 3600):
        event(request, "rate_limited")
    else:
        candidate = CaptiveOTP.objects.filter(challenge_hash=digest(challenge), proxy=request.captive_proxy).values_list("user_id", flat=True).first()
        with transaction.atomic():
            if candidate:
                CaptivePortalUser.objects.filter(pk=candidate).update(updated_at=F("updated_at"))
            # Increment before reading: this is the SQLite write lock and an atomic
            # attempt reservation. Concurrent successful submissions cannot reuse OTPs.
            records = CaptiveOTP.objects.filter(challenge_hash=digest(challenge), proxy=request.captive_proxy,
                client_fingerprint=binding, delivered=True, consumed_at__isnull=True, expires_at__gt=timezone.now(),
                failed_attempts__lt=settings.CAPTIVE_MAX_ATTEMPTS)
            reserved = records.update(failed_attempts=F("failed_attempts") + 1)
            record = CaptiveOTP.objects.select_related("user").filter(challenge_hash=digest(challenge), proxy=request.captive_proxy).first()
            matches = constant_time_compare(record.otp_hash if record else "0" * 64, digest(challenge + ":" + otp, "otp"))
            identifier_matches = False
            if record and identifier_valid:
                registered_identifier = record.user.email_address if is_email else record.user.mobile_number
                identifier_matches = constant_time_compare(registered_identifier or "", identifier)
            if reserved and matches and identifier_matches and record.user.authorized(request.captive_proxy):
                record.consumed_at = timezone.now()
                record.failed_attempts -= 1
                record.save(update_fields=["consumed_at", "failed_attempts"])
                previous = current_session(request)
                if previous:
                    CaptiveSession.objects.filter(pk=previous.pk).update(revoked=True)
                    event(request, "session_revoked", previous.user)
                token = secrets.token_urlsafe(32)
                CaptiveSession.objects.create(user=record.user, proxy=request.captive_proxy, token_hash=digest(token),
                    expires_at=timezone.now() + timedelta(seconds=settings.CAPTIVE_SESSION_SECONDS),
                    client_ip=request.captive_ip, user_agent=request.headers.get("User-Agent", "")[:512],
                    client_fingerprint=session_fingerprint(request))
                CaptivePortalUser.objects.filter(pk=record.user_id).update(last_successful_login=timezone.now())
                event(request, "otp_verification_succeeded", record.user, record.channel)
                event(request, "session_created", record.user, record.channel)
            else:
                event(request, "otp_verification_failed", record.user if record else None, record.channel if record else "")
    if token:
        response = HttpResponseRedirect(local_next(request.POST.get("next")))
        response.set_cookie(COOKIE, token, max_age=settings.CAPTIVE_SESSION_SECONDS, secure=True,
                            httponly=True, samesite="Lax", path="/")
        return response
    return page(request, challenge=challenge, identifier=request.POST.get("identifier", ""),
                error="The code is invalid or expired. Please try again or request a new code.")


@require_POST
@never_cache
def logout(request):
    session = current_session(request)
    if session:
        CaptiveSession.objects.filter(pk=session.pk).update(revoked=True)
        event(request, "session_revoked", session.user)
    response = HttpResponseRedirect("/_captive/login/")
    response.set_cookie(COOKIE, "", max_age=0, secure=True, httponly=True, samesite="Lax", path="/")
    return response


@require_GET
@never_cache
def status(request):
    authenticated = bool(current_session(request))
    return JsonResponse({"authenticated": authenticated}, status=200 if authenticated else 401)
