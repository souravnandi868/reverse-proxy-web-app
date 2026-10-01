"""SMS transport. The organization supplies the small gateway adapter contract."""
import http.client
from urllib.parse import urlsplit
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from django.views.decorators.debug import sensitive_variables


class GatewayAdapter:
    """Implement using the gateway's actual specification, never a guessed schema."""

    def build_request(self, *, path, mobile, otp, token, sender_id, template_id):
        """Return (method, path/query, headers dict, body bytes).

        The configured HTTPS origin is fixed. Adapters may encode query parameters
        for GET gateways or construct a JSON/form body for POST gateways.
        """
        raise ImproperlyConfigured("Configure CAPTIVE_SMS_HTTP_ADAPTER with the organization's gateway adapter.")

    def accepted(self, status, headers, body):
        """Return True only for documented gateway acceptance (not merely HTTP 200)."""
        raise ImproperlyConfigured("The gateway acceptance format must be implemented.")


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
            mobile=mobile, otp=otp, token=settings.CAPTIVE_SMS_API_TOKEN,
            sender_id=settings.CAPTIVE_SMS_SENDER_ID, template_id=settings.CAPTIVE_SMS_TEMPLATE_ID)
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or any(ord(c) < 32 for c in path):
            raise ImproperlyConfigured("Gateway adapter must return a local request path.")
        conn = http.client.HTTPSConnection(url.hostname, url.port, timeout=5)
        try:
            conn.connect()
            conn.sock.settimeout(10)
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
