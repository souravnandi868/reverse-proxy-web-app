"""Opt-in real TLS/NGINX smoke test. Run: python tests/nginx_captive_integration.py /path/to/nginx

Uses isolated SQLite, temporary certificates, loopback listeners and no SMS gateway.
Does not modify any installed NGINX configuration or application database.
"""
import http.client
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode
from unittest.mock import patch
from wsgiref.simple_server import make_server, WSGIRequestHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "proxyadmin.settings")
import django
django.setup()
from django.conf import settings
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from django.contrib.auth import get_user_model
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from proxies.models import ProxyConfig, CertificateBundle
from proxies.captive_models import CaptivePortalUser
from proxies.services import render_proxy_config
from proxies.captive import route_key


class QuietWSGI(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class Backend(BaseHTTPRequestHandler):
    def do_GET(self):
        content = json.dumps({"path": self.path, "cookie": self.headers.get("Cookie"), "upgrade": self.headers.get("Upgrade")}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *args):
        pass


def main():
    binary = str(Path(sys.argv[1]).resolve())
    with tempfile.TemporaryDirectory(prefix="captive-nginx-") as folder:
        root = Path(folder)
        (root / "logs").mkdir()
        (root / "temp").mkdir()
        settings.DATABASES["default"]["NAME"] = root / "test.sqlite3"
        settings.ALLOWED_HOSTS = ["app.example.com"]
        settings.MEDIA_ROOT = root
        from django.http import HttpResponse
        settings.CSRF_FAILURE_VIEW = lambda request, reason="": HttpResponse(reason, status=403)
        call_command("migrate", verbosity=0)
        backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
        application = make_server("127.0.0.1", 0, get_wsgi_application(), handler_class=QuietWSGI)
        settings.CAPTIVE_ADMIN_UPSTREAM = f"http://127.0.0.1:{application.server_port}"
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        settings.CSRF_TRUSTED_ORIGINS = [f"https://app.example.com:{port}"]
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "app.example.com")])
        cert = x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key()).serial_number(
            x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1)).not_valid_after(
            datetime.now(timezone.utc) + timedelta(days=1)).sign(key, hashes.SHA256())
        (root / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (root / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        admin = get_user_model().objects.create_user("integration")
        bundle = CertificateBundle.objects.create(name="integration", certificate="cert.pem", private_key="key.pem", uploaded_by=admin)
        proxy = ProxyConfig.objects.create(domain_name="app.example.com", backend_private_ip="127.0.0.1", backend_port=backend.server_port,
            captive_portal_enabled=True, websocket_enabled=True, certificate_bundle=bundle, created_by=admin, updated_by=admin)
        user = CaptivePortalUser.objects.create(name="Integration", section="Test", rank="Test", mobile_number="9876543210", all_fqdns=True)
        config = render_proxy_config(proxy).replace("listen 443 ssl;", f"listen 127.0.0.1:{port} ssl;")
        config = config.replace("/var/log/nginx/proxy-admin/", root.as_posix() + "/logs/")
        config = config.replace(str(root), root.as_posix()).replace("\\cert.pem", "/cert.pem").replace("\\key.pem", "/key.pem")
        (root / "nginx.conf").write_text("daemon off;\nmaster_process off;\nevents {}\nhttp {\n" + config + "\n}\n")
        args = [binary, "-p", root.as_posix() + "/", "-c", "nginx.conf"]
        from django.db import connections
        connections.close_all()
        checked = subprocess.run(args + ["-t"], capture_output=True, text=True, timeout=15)
        assert checked.returncode == 0, checked.stderr
        print("PASS: generated NGINX configuration passes nginx -t")
        for server in (backend, application):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        cookies = SimpleCookie()

        def request(path, data=None, headers=None, include_cookies=True):
            connection = http.client.HTTPSConnection("127.0.0.1", port, context=ssl._create_unverified_context(), timeout=10)
            supplied = {"Host": "app.example.com", **(headers or {})}
            if include_cookies:
                supplied["Cookie"] = "; ".join(f"{k}={v.value}" for k, v in cookies.items())
            body = None
            if data is not None:
                body = urlencode(data)
                supplied.update({"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://app.example.com"})
            connection.request("POST" if data is not None else "GET", path, body, supplied)
            response = connection.getresponse()
            content = response.read().decode()
            result = response.status, dict(response.getheaders()), content
            for name, value in response.getheaders():
                if name.lower() == "set-cookie":
                    cookies.load(value)
            connection.close()
            return result

        try:
            for attempt in range(50):
                try:
                    status, headers, _ = request("/reports?x=1&y=two%20words")
                    break
                except OSError:
                    time.sleep(.1)
            assert status == 302, (status, headers)
            assert headers["Location"] == "/_captive/login/?next=%2Freports%3Fx%3D1%26y%3Dtwo%2520words", headers
            assert request("/_captive_auth")[0] == 404
            assert request("/_captive/check/")[0] == 404
            assert request("/reports", headers={"Accept": "application/json"})[0] == 401
            assert request("/socket", headers={"Upgrade": "websocket", "Connection": "Upgrade"})[0] == 302
            print("PASS: browser redirect, original query encoding, internal endpoint protection, JSON 401 and unauthenticated WebSocket gating")
            status, _, content = request(headers["Location"])
            assert status == 200
            csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', content).group(1)
            with patch("proxies.captive.deliver") as deliver:
                status, _, content = request("/_captive/send-otp/", {"csrfmiddlewaretoken": csrf, "destination": "9876543210"})
            assert status == 200 and deliver.called
            challenge = re.search(r'name="challenge" value="([^"]+)"', content).group(1)
            status, headers, _ = request("/_captive/verify-otp/", {"csrfmiddlewaretoken": csrf, "challenge": challenge,
                "otp": deliver.call_args.args[2], "next": "/reports?x=1&y=two%20words"})
            assert status == 302 and headers["Location"] == "/reports?x=1&y=two%20words"
            cookies["backend_cookie"] = "preserved"
            status, _, content = request(headers["Location"])
            payload = json.loads(content)
            assert status == 200 and payload["path"] == "/reports?x=1&y=two%20words"
            assert "__Host-captive_session" not in payload["cookie"] and "backend_cookie=preserved" in payload["cookie"], payload
            status, _, content = request("/socket", headers={"Upgrade": "websocket", "Connection": "Upgrade"})
            assert status == 200 and json.loads(content)["upgrade"] == "websocket"
            print("PASS: real CSRF + OTP login, secure cookie, backend forwarding, session-cookie stripping and authenticated Upgrade forwarding")
            request("/_captive/logout/", {"csrfmiddlewaretoken": csrf})
            assert request("/reports")[0] == 302
            print("PASS: logout revokes backend access")
            if "--browser" in sys.argv:
                from playwright.sync_api import sync_playwright, expect
                from proxies.captive_models import CaptiveRateLimit
                CaptiveRateLimit.objects.all().delete()
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="chrome", headless=True, args=[
                        "--host-resolver-rules=MAP app.example.com 127.0.0.1", "--no-proxy-server"])
                    page = browser.new_page(ignore_https_errors=True, viewport={"width": 375, "height": 812})
                    page.goto(f"https://app.example.com:{port}/reports?browser=1")
                    expect(page.get_by_role("heading", name="app.example.com")).to_be_visible()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    page.get_by_role("button", name="Switch color theme").click()
                    assert page.locator("html").get_attribute("data-console-theme") == "light"
                    page.get_by_label("Registered mobile number or email address").fill("9876543210")
                    with patch("proxies.captive.deliver") as deliver:
                        page.get_by_role("button", name="Send OTP", exact=True).click()
                        expect(page.get_by_label("Six-digit verification code")).to_be_visible()
                    expect(page.get_by_role("button", name="Send OTP", exact=True)).to_be_disabled()
                    page.get_by_label("Six-digit verification code").fill(deliver.call_args.args[2])
                    page.get_by_role("button", name="Verify and continue").click()
                    expect(page.locator("body")).to_contain_text('"path": "/reports?browser=1"')
                    browser.close()
                print("PASS: mobile-width Chrome portal, theme switch, resend countdown and browser OTP login")
        finally:
            process.terminate()
            process.wait(timeout=10)
            for server in (backend, application):
                server.shutdown()
                server.server_close()
            from django.db import connections
            connections.close_all()


if __name__ == "__main__":
    main()
