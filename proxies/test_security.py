from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from .captive import COOKIE, route_key
from .captive_models import CaptiveRateLimit, CaptiveSession
from . import test_captive


@override_settings(ALLOWED_HOSTS=["app.example.com", "other.example.com", "testserver"])
class PortalSecurityTests(TestCase):
    setUp = test_captive.CaptiveTests.setUp
    send = test_captive.CaptiveTests.send
    authenticate = test_captive.CaptiveTests.authenticate

    @override_settings(AUTH_CAPTCHA_SESSION_LIMIT=2)
    def test_captcha_reload_limit(self):
        self.assertEqual(self.client.get("/_captive/login/").status_code, 200)
        self.assertEqual(self.client.get("/_captive/login/").status_code, 200)
        response = self.client.get("/_captive/login/")
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)

    @override_settings(AUTH_POST_SESSION_LIMIT=2)
    def test_invalid_captcha_attempts_are_limited_before_delivery(self):
        with patch("proxies.captive.deliver") as delivery:
            for _ in range(2):
                self.assertEqual(self.client.post("/_captive/send-otp/", {}).status_code, 200)
            self.assertEqual(self.client.post("/_captive/send-otp/", {}).status_code, 429)
        delivery.assert_not_called()

    def test_copied_otp_challenge_cannot_be_used_in_another_browser(self):
        response, delivery = self.send()
        data = {"challenge": response.context["challenge"], "otp": delivery.call_args.args[1],
                "identifier": "9999999999"}
        thief = Client(HTTP_HOST=self.proxy.domain_name, HTTP_X_CAPTIVE_KEY=route_key(self.proxy.domain_name),
                       HTTP_X_CAPTIVE_IP="192.0.2.10")
        denied = thief.post("/_captive/verify-otp/", data)
        self.assertNotIn(COOKIE, denied.cookies)
        self.assertEqual(CaptiveSession.objects.count(), 0)
        accepted = self.client.post("/_captive/verify-otp/", data)
        self.assertEqual(accepted.status_code, 302)

    def test_copied_session_cookie_with_changed_agent_is_revoked(self):
        self.authenticate()
        self.assertEqual(self.client.get("/_captive/status/").status_code, 200)
        self.assertEqual(self.client.get("/_captive/status/", HTTP_USER_AGENT="stolen-device").status_code, 401)
        self.assertTrue(CaptiveSession.objects.get().revoked)

    def test_session_ip_binding(self):
        self.authenticate()
        self.assertEqual(self.client.get("/_captive/status/", HTTP_X_CAPTIVE_IP="192.0.2.11").status_code, 401)

    @override_settings(CAPTIVE_BIND_SESSION_IP=False)
    def test_mobile_ip_change_can_be_allowed_explicitly(self):
        self.authenticate()
        self.assertEqual(self.client.get("/_captive/status/", HTTP_X_CAPTIVE_IP="192.0.2.11").status_code, 200)

    def test_idle_session_is_revoked(self):
        self.authenticate()
        CaptiveSession.objects.update(last_used_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(self.client.get("/_captive/status/").status_code, 401)
        self.assertTrue(CaptiveSession.objects.get().revoked)

    def test_csp_nonce_and_secure_browser_cookie(self):
        response = self.client.get("/_captive/login/")
        csp = response["Content-Security-Policy"]
        self.assertIn("object-src 'none'", csp)
        self.assertNotIn("unsafe-inline", csp.split("script-src ")[1].split(";")[0])
        self.assertContains(response, 'nonce="' + response.wsgi_request.csp_nonce + '"')
        self.assertNotContains(response, 'onclick=')
        from django.conf import settings
        self.assertTrue(response.cookies[settings.SESSION_COOKIE_NAME]["secure"])

    def test_csrf_rejection_has_security_headers(self):
        self.client.handler.enforce_csrf_checks = True
        response = self.client.post("/_captive/send-otp/", {})
        self.assertEqual(response.status_code, 403)
        self.assertIn("Content-Security-Policy", response)

    def test_nginx_edge_limits_and_private_cookie_stripping(self):
        from .services import render_proxy_config
        from django.conf import settings
        config = render_proxy_config(self.proxy)
        for directive in ("limit_req_zone", "limit_conn_zone", "limit_req_status 429;",
                          "limit_conn_status 429;", settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME):
            self.assertIn(directive, config)
        self.assertIn("proxy_admin_session|proxy_admin_csrf)=", config)

    def test_database_limiter_failure_blocks_authentication(self):
        from django.db import OperationalError
        with patch("proxies.captive.limit", side_effect=OperationalError("unavailable")):
            response = self.client.get("/_captive/login/")
        self.assertEqual(response.status_code, 503)


class ConsoleSecurityTests(TestCase):
    @override_settings(AUTH_CAPTCHA_IP_LIMIT=2)
    def test_new_cookies_and_forwarded_header_do_not_bypass_ip_limit(self):
        for i in range(2):
            response = Client().get("/login/", HTTP_X_FORWARDED_FOR=f"192.0.2.{i}")
            self.assertEqual(response.status_code, 200)
        self.assertEqual(Client().get("/login/", HTTP_X_FORWARDED_FOR="198.51.100.1").status_code, 429)

    @override_settings(AUTH_USERNAME_LIMIT=2)
    def test_username_limit_survives_ip_and_session_changes(self):
        for i in range(2):
            self.assertEqual(Client().post("/login/", {"username": "operator", "password": "wrong"},
                                          REMOTE_ADDR=f"192.0.2.{i}").status_code, 200)
        response = Client().post("/login/", {"username": "OPERATOR", "password": "wrong"}, REMOTE_ADDR="198.51.100.1")
        self.assertEqual(response.status_code, 429)

    def test_console_session_agent_binding_and_idle_expiry(self):
        user = get_user_model().objects.create_user("operator", password="valid-test-password", is_staff=True)
        for case in ("agent", "idle"):
            client = Client()
            client.force_login(user)
            client.get("/")
            if case == "idle":
                session = client.session
                session["auth_last_activity"] = 0
                session.save()
                response = client.get("/")
            else:
                response = client.get("/", HTTP_USER_AGENT="changed-agent")
            self.assertEqual(response.status_code, 401)
            self.assertNotIn("_auth_user_id", client.session)


class DeploymentSecurityChecks(TestCase):
    @override_settings(SECRET_KEY="dev-only-change-me")
    def test_development_secret_is_rejected_for_deployment(self):
        from .security_checks import authentication_deployment_checks
        self.assertIn("proxies.E001", [error.id for error in authentication_deployment_checks(None)])

    @override_settings(AUTH_POST_IP_LIMIT=0)
    def test_nonpositive_limit_is_rejected(self):
        from .security_checks import authentication_deployment_checks
        self.assertIn("proxies.E002", [error.id for error in authentication_deployment_checks(None)])
