"""Authentication abuse controls; never trust public forwarding headers."""
import ipaddress
import secrets
import time

from django.conf import settings
from django.contrib.auth import logout
from django.db import DatabaseError
from django.http import HttpResponse
from django.utils.crypto import constant_time_compare, salted_hmac


def security_digest(value, purpose):
    return salted_hmac("authentication." + purpose, value, algorithm="sha256").hexdigest()


def client_ip(request):
    if hasattr(request, "captive_ip"):
        return request.captive_ip
    # The console must use a trusted ingress configuration. Forwarded headers
    # from arbitrary clients must not let callers choose their own rate bucket.
    try:
        return str(ipaddress.ip_address(request.META.get("REMOTE_ADDR", "")))
    except ValueError:
        return "unknown"


def session_fingerprint(request):
    # A passive signal, not proof of device identity; combine with token/IP.
    return security_digest(request.headers.get("User-Agent", "")[:512], "agent")


def browser_identity(request):
    identity = request.session.get("auth_browser_identity")
    if not isinstance(identity, str) or len(identity) != 43:
        identity = secrets.token_urlsafe(32)
        request.session["auth_browser_identity"] = identity
    return identity


def browser_fingerprint(request):
    return security_digest(browser_identity(request) + ":" + session_fingerprint(request), "browser")


def blocked(status=429):
    response = HttpResponse("Too many authentication requests. Please wait and try again." if status == 429
                            else "Authentication is temporarily unavailable. Please try again later.",
                            status=status, content_type="text/plain")
    response["Retry-After"] = str(settings.AUTH_RATE_WINDOW_SECONDS if status == 429 else 30)
    response["Cache-Control"] = "no-store, private"
    return response


class BrowserSecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(24)
        response = self.get_response(request)
        response["X-Content-Type-Options"] = "nosniff"
        response["X-Frame-Options"] = "DENY"
        response["Referrer-Policy"] = "same-origin"
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        response["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'nonce-" + request.csp_nonce + "'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        return response


class AuthenticationSecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        captive = hasattr(request, "captive_proxy")
        path = request.path
        console_login = path in {"/login/", "/admin/login/"}
        captcha_page = path == "/_captive/login/" or console_login
        auth_post = request.method == "POST" and (console_login or path in {
            "/_captive/send-otp/", "/_captive/verify-otp/", "/_captive/logout/"})
        if (request.method == "GET" and captcha_page) or auth_post:
            from .captive import limit
            ip = client_ip(request)
            identity = browser_identity(request)
            namespace = str(request.captive_proxy.pk) if captive else "console"
            operation = "captcha" if request.method == "GET" else "post:" + path
            prefix = "auth:" + namespace + ":" + operation + ":"
            ip_max = settings.AUTH_CAPTCHA_IP_LIMIT if request.method == "GET" else settings.AUTH_POST_IP_LIMIT
            session_max = settings.AUTH_CAPTCHA_SESSION_LIMIT if request.method == "GET" else settings.AUTH_POST_SESSION_LIMIT
            try:
                network = str(ipaddress.ip_network(ip + ("/24" if ":" not in ip else "/64"), strict=False))
            except ValueError:
                network = "unknown"
            passive_fingerprint = security_digest(network + ":" + session_fingerprint(request), "rate-device")
            buckets = [(prefix + "ip:" + ip, ip_max),
                       (prefix + "session:" + identity, session_max),
                       (prefix + "fingerprint:" + passive_fingerprint, settings.AUTH_FINGERPRINT_LIMIT)]
            # Usernames have their own bucket so switching IP/cookies is insufficient.
            if auth_post and console_login:
                username = request.POST.get("username", "")[:254].strip().casefold()
                buckets.append(("auth:username:" + username, settings.AUTH_USERNAME_LIMIT))
            try:
                allowed = all([limit(key, maximum, settings.AUTH_RATE_WINDOW_SECONDS)
                               for key, maximum in buckets])
            except DatabaseError:
                # An unavailable shared limiter must not silently permit login.
                return blocked(503)
            if not allowed:
                return blocked()
        if not captive and request.user.is_authenticated:
            now = time.time()
            previous = request.session.get("auth_last_activity", now)
            fingerprint = session_fingerprint(request)
            old_fingerprint = request.session.get("auth_session_fingerprint", fingerprint)
            if (now - previous > settings.AUTH_SESSION_IDLE_SECONDS
                    or not constant_time_compare(old_fingerprint, fingerprint)):
                logout(request)
                response = HttpResponse("Session expired. Please sign in again.", status=401)
                response["Cache-Control"] = "no-store, private"
                return response
            request.session["auth_last_activity"] = now
            request.session["auth_session_fingerprint"] = fingerprint
        response = self.get_response(request)
        # Bind newly authenticated console sessions before their next request.
        if not captive and request.user.is_authenticated:
            request.session.setdefault("auth_session_fingerprint", session_fingerprint(request))
            request.session.setdefault("auth_last_activity", time.time())
        if captcha_page or auth_post or (not captive and request.user.is_authenticated):
            response["Cache-Control"] = "no-store, private"
        return response
