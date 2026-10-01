"""SMS transport for the configured organization gateway."""
import http.client
import json
from urllib.parse import urlencode, urlsplit
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from django.views.decorators.debug import sensitive_variables


class GatewayAdapter:
    """Kolkata Police form-encoded SMS API adapter."""

    def build_request(self, *, path, mobile, otp):
        """Return the provider's unauthenticated POST form request."""
        if not mobile.startswith("+91") or len(mobile) != 13 or not mobile[3:].isdigit():
            raise ImproperlyConfigured("SMS gateway requires a normalized Indian mobile number.")
        if len(otp) != 6 or not otp.isdigit():
            raise ImproperlyConfigured("SMS OTP must contain six digits.")
        template = settings.CAPTIVE_SMS_MESSAGE_TEMPLATE
        if template.count("{#var#}") != 1:
            raise ImproperlyConfigured("SMS message template must contain exactly one {#var#} placeholder.")
        message = template.replace("{#var#}", otp)
        body = urlencode({"mobileno": mobile[3:], "message": message}).encode("ascii")
        return "POST", path, {"Content-Type": "application/x-www-form-urlencoded"}, body

    def accepted(self, status, headers, body):
        """Require the observed gateway response: status 1 and message Success."""
        if not 200 <= status < 300:
            return False
        try:
            result = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        return (isinstance(result, dict) and str(result.get("status")) == "1"
                and str(result.get("message", "")).strip().casefold() == "success")


class DevelopmentSMS:
    def send(self, mobile, otp):
        # Deliberately no console output, log, filesystem or plaintext outbox.
        if not settings.DEBUG:
            raise ImproperlyConfigured("Development SMS requires DEBUG.")


class HTTPSMS:
    @sensitive_variables()
    def send(self, mobile, otp):
        url = urlsplit(settings.CAPTIVE_SMS_API_URL)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
            raise ImproperlyConfigured("SMS gateway must be an HTTPS URL without credentials or fragment.")
        adapter = import_string(settings.CAPTIVE_SMS_HTTP_ADAPTER)()
        method, path, headers, body = adapter.build_request(
            path=(url.path or "/") + ("?" + url.query if url.query else ""),
            mobile=mobile, otp=otp)
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or any(ord(c) < 32 for c in path):
            raise ImproperlyConfigured("Gateway adapter must return a local request path.")
        conn = http.client.HTTPSConnection(url.hostname, url.port, timeout=5)
        try:
            conn.connect()
            conn.sock.settimeout(10)
            headers = {**headers, "Connection": "close"}
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            content = response.read(65537)
            if len(content) > 65536 or not adapter.accepted(response.status, dict(response.getheaders()), content):
                raise RuntimeError("SMS gateway did not accept delivery.")
        finally:
            conn.close()


@sensitive_variables()
def deliver(destination, otp):
    backend = {"http": HTTPSMS, "development": DevelopmentSMS}.get(settings.CAPTIVE_SMS_BACKEND)
    if backend is None:
        raise ImproperlyConfigured("Configure CAPTIVE_SMS_BACKEND.")
    backend().send(destination, otp)
