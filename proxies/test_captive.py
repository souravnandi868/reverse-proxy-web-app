from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .captive import COOKIE, GENERIC, digest, local_next, route_key, limit
from .captive_admin import AuthorizedUserForm
from .captive_models import CaptivePortalUser, CaptiveOTP, CaptiveSession, CaptiveAudit, CaptiveRateLimit
from .models import ProxyConfig, CertificateBundle
from .services import render_proxy_config


@override_settings(ALLOWED_HOSTS=["app.example.com", "other.example.com", "testserver"], CAPTIVE_RESEND_SECONDS=60)
class CaptiveTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("operator", "admin@example.com", "secret")
        self.cert = CertificateBundle.objects.create(name="test", certificate="cert.pem", private_key="key.pem", uploaded_by=self.admin)
        self.proxy = ProxyConfig.objects.create(domain_name="app.example.com", backend_private_ip="10.0.0.1", backend_port=8080,
            certificate_bundle=self.cert, captive_portal_enabled=True, created_by=self.admin, updated_by=self.admin)
        self.other = ProxyConfig.objects.create(domain_name="other.example.com", backend_private_ip="10.0.0.2", backend_port=8080,
            certificate_bundle=self.cert, captive_portal_enabled=True, created_by=self.admin, updated_by=self.admin)
        self.user = CaptivePortalUser.objects.create(name="Test Person", section="IT", rank="Officer", mobile_number="9999999999", email_address="test@example.com")
        self.user.proxies.add(self.proxy)
        self.client = Client(HTTP_HOST=self.proxy.domain_name, HTTP_X_CAPTIVE_KEY=route_key(self.proxy.domain_name), HTTP_X_CAPTIVE_IP="192.0.2.10")

    def send(self, identifier="9999999999"):
        with patch("proxies.captive.deliver") as delivery:
            response = self.client.post("/_captive/send-otp/", {"identifier": identifier, "next": "/reports?a=1&b=two%20words"})
        return response, delivery

    def authenticate(self, identifier="9999999999"):
        response, delivery = self.send(identifier)
        otp = delivery.call_args.args[1]
        return self.client.post("/_captive/verify-otp/", {"challenge": response.context["challenge"], "otp": otp, "next": response.context["next"], "identifier": identifier})

    def test_default_and_non_captive_output_unchanged(self):
        self.assertFalse(ProxyConfig().captive_portal_enabled)
        self.proxy.captive_portal_enabled = False
        config = render_proxy_config(self.proxy)
        self.assertNotIn("captive", config)
        self.assertIn("proxy_read_timeout 60s;", config)
        self.assertIn("proxy_admin;", config)

    def test_captive_nginx_websocket_and_privacy(self):
        self.proxy.websocket_enabled = True
        config = render_proxy_config(self.proxy)
        for directive in ["auth_request /_captive_auth;", "internal;", "proxy_set_header Upgrade $http_upgrade;",
                          "proxy_read_timeout 3600s;", "ssl_certificate", "proxy_set_header X-Original-URI $request_uri;",
                          "error_page 401 = @captive_login;", "proxy_cache off;", "proxy_set_header Cookie $captive_cookie_"]:
            self.assertIn(directive, config)
        self.assertEqual(config.count("auth_request /_captive_auth;"), 1)
        self.assertIn("location = /_captive/login/", config)
        self.assertNotIn('"request":"$request"', config)
        self.assertNotIn("$http_authorization", config)

    def test_ingress_rejects_forged_headers_and_unregistered_host(self):
        self.assertEqual(Client(HTTP_HOST="app.example.com").get("/_captive/login/").status_code, 404)
        self.assertEqual(self.client.get("/_captive/login/", HTTP_HOST="other.example.com").status_code, 404)
        self.assertEqual(self.client.get("/_captive/login/", HTTP_HOST="evil.example.com").status_code, 400)
        self.assertEqual(self.client.get("/_captive/check/").status_code, 404)

    def test_entry_redirect_encodes_full_local_path_and_json_401(self):
        response = self.client.get("/_captive/entry/", HTTP_X_CAPTIVE_INTERNAL="1", HTTP_X_ORIGINAL_URI="/a?x=1&y=2")
        self.assertEqual(response.url, "/_captive/login/?next=%2Fa%3Fx%3D1%26y%3D2")
        response = self.client.get("/_captive/entry/", HTTP_X_CAPTIVE_INTERNAL="1", HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "authentication_required")

    def test_public_pages_no_loops_and_no_cache(self):
        response = self.client.get("/_captive/login/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertNotIn("https://", response.content.decode())
        self.assertEqual(self.client.get("/_captive/status/").status_code, 401)

    def test_valid_sms_cookie_hash_and_one_time_use(self):
        response = self.authenticate()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/reports?a=1&b=two%20words")
        cookie = response.cookies[COOKIE]
        for attr in ("secure", "httponly"):
            self.assertTrue(cookie[attr])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], "/")
        self.assertEqual(cookie["domain"], "")
        session = CaptiveSession.objects.get()
        self.assertEqual(session.token_hash, digest(cookie.value))
        self.assertNotEqual(session.token_hash, cookie.value)
        self.assertTrue(CaptiveOTP.objects.get().consumed_at)
        self.assertEqual(self.client.get("/_captive/check/", HTTP_X_CAPTIVE_INTERNAL="1").status_code, 204)
        self.assertTrue(CaptiveAudit.objects.filter(action="session_created").exists())

    def test_mobile_and_case_insensitive_email_both_send_sms_to_registered_mobile(self):
        for identifier in ("9999999999", "+91 99999 99999", "TEST@EXAMPLE.COM"):
            with self.subTest(identifier=identifier):
                CaptiveOTP.objects.all().delete()
                CaptiveRateLimit.objects.all().delete()
                response, delivery = self.send(identifier)
                self.assertEqual(response.context["message"], GENERIC)
                self.assertEqual(delivery.call_args.args[0], "+919999999999")
                self.assertNotEqual(delivery.call_args.args[0], "test@example.com")
                self.assertEqual(CaptiveOTP.objects.get().channel, "sms")
                if "@" in identifier:
                    self.assertNotContains(response, "+919999999999")
                verified = self.client.post("/_captive/verify-otp/", {
                    "challenge": response.context["challenge"], "otp": delivery.call_args.args[1],
                    "identifier": identifier,
                })
                self.assertEqual(verified.status_code, 302)

    def test_otp_challenge_cannot_be_used_with_another_users_identifier(self):
        other_user = CaptivePortalUser.objects.create(name="Another Person", section="IT", rank="Officer",
            mobile_number="8765432109", email_address="another@example.com")
        other_user.proxies.add(self.proxy)
        response, delivery = self.send("another@example.com")
        challenge = response.context["challenge"]
        otp = delivery.call_args.args[1]
        rejected = self.client.post("/_captive/verify-otp/", {
            "challenge": challenge, "otp": otp, "identifier": "9999999999",
        })
        self.assertEqual(rejected.status_code, 200)
        self.assertFalse(CaptiveSession.objects.exists())
        accepted = self.client.post("/_captive/verify-otp/", {
            "challenge": challenge, "otp": otp, "identifier": "another@example.com",
        })
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(CaptiveSession.objects.get().user, other_user)

    def test_bad_expired_exhausted_and_reused_codes(self):
        response, delivery = self.send()
        data = {"challenge": response.context["challenge"], "otp": delivery.call_args.args[1], "identifier": "9999999999"}
        wrong = "000000" if data["otp"] != "000000" else "999999"
        for _ in range(5):
            self.assertEqual(self.client.post("/_captive/verify-otp/", {**data, "otp": wrong}).status_code, 200)
        self.assertEqual(self.client.post("/_captive/verify-otp/", data).status_code, 200)
        self.assertEqual(CaptiveOTP.objects.get().failed_attempts, 5)
        self.assertFalse(CaptiveSession.objects.exists())
        CaptiveOTP.objects.update(failed_attempts=0, expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.post("/_captive/verify-otp/", data).status_code, 200)
        CaptiveOTP.objects.update(expires_at=timezone.now() + timedelta(seconds=60))
        self.assertEqual(self.client.post("/_captive/verify-otp/", data).status_code, 302)
        self.assertEqual(self.client.post("/_captive/verify-otp/", data).status_code, 200)
        self.assertEqual(CaptiveSession.objects.count(), 1)

    def test_generic_response_for_disabled_unknown_deleted_unassigned(self):
        known, _ = self.send()
        self.user.is_enabled = False
        self.user.save()
        disabled, delivery = self.send()
        self.assertFalse(delivery.called)
        unknown, delivery = self.send("8765432109")
        self.assertFalse(delivery.called)
        for response in (known, disabled, unknown):
            self.assertEqual(response.context["message"], GENERIC)
        self.user.is_enabled = True
        self.user.save()
        self.user.proxies.clear()
        CaptiveRateLimit.objects.all().delete()
        _, delivery = self.send()
        self.assertFalse(delivery.called)
        self.user.all_fqdns = True
        self.user.save()
        CaptiveRateLimit.objects.all().delete()
        _, delivery = self.send()
        self.assertTrue(delivery.called)
        self.user.delete()
        CaptiveRateLimit.objects.all().delete()
        _, delivery = self.send()
        self.assertFalse(delivery.called)

    def test_rate_limits_resend_and_new_code_invalidation(self):
        self.send()
        _, delivery = self.send()
        self.assertFalse(delivery.called)
        self.assertTrue(CaptiveAudit.objects.filter(action="rate_limited").exists())
        CaptiveRateLimit.objects.update(window_start=timezone.now() - timedelta(hours=2))
        self.send()
        self.assertEqual(CaptiveOTP.objects.filter(consumed_at__isnull=True).count(), 1)
        for i in range(5):
            self.assertTrue(limit("mobile-independent", 5, 3600))
        self.assertFalse(limit("mobile-independent", 5, 3600))
        with override_settings(CAPTIVE_IP_SEND_LIMIT=1):
            _, delivery = self.send("8765432109")
            self.assertFalse(delivery.called)

    def test_gateway_failure_cannot_verify(self):
        with patch("proxies.captive.deliver", side_effect=RuntimeError("secret payload")), patch("proxies.captive.secrets.randbelow", return_value=123456):
            response = self.client.post("/_captive/send-otp/", {"identifier": "9999999999"})
        self.assertNotContains(response, "secret payload")
        response = self.client.post("/_captive/verify-otp/", {"challenge": response.context["challenge"], "otp": "123456", "identifier": "9999999999"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CaptiveSession.objects.exists())
        self.assertTrue(CaptiveAudit.objects.filter(action="otp_delivery_failed").exists())

    def test_fqdn_binding_even_when_cookie_is_copied(self):
        self.authenticate()
        self.user.all_fqdns = True
        # Use update to retain the old session for this cross-FQDN test.
        CaptivePortalUser.objects.filter(pk=self.user.pk).update(all_fqdns=True)
        response = self.client.get("/_captive/status/", HTTP_HOST=self.other.domain_name, HTTP_X_CAPTIVE_KEY=route_key(self.other.domain_name))
        self.assertEqual(response.status_code, 401)

    def test_logout_revokes_and_expires_cookie(self):
        self.authenticate()
        response = self.client.post("/_captive/logout/")
        self.assertEqual(response.cookies[COOKIE]["max-age"], 0)
        self.assertTrue(CaptiveSession.objects.get().revoked)
        self.assertEqual(self.client.get("/_captive/status/").status_code, 401)
        self.assertEqual(self.client.get("/_captive/logout/").status_code, 405)

    def test_disabling_contact_change_and_assignment_remove_revoke(self):
        for change in ("disabled", "contact", "assignment"):
            with self.subTest(change=change):
                self.user.is_enabled = True
                self.user.mobile_number = "9999999999"
                self.user.save()
                self.user.proxies.add(self.proxy)
                CaptiveRateLimit.objects.all().delete()
                self.authenticate()
                if change == "disabled":
                    self.user.is_enabled = False
                    self.user.save()
                elif change == "contact":
                    self.user.email_address = "changed@example.com"
                    self.user.save()
                else:
                    self.user.proxies.remove(self.proxy)
                self.assertFalse(CaptiveSession.objects.filter(revoked=False).exists())
                self.assertFalse(CaptiveOTP.objects.filter(consumed_at__isnull=True).exists())

    def test_mobile_and_email_changes_revoke_sessions_and_pending_otps(self):
        for field, value, old_identifier in (
            ("mobile_number", "8765432109", "9999999999"),
            ("email_address", "changed@example.com", "test@example.com"),
        ):
            with self.subTest(field=field):
                self.user.mobile_number = "9999999999"
                self.user.email_address = "test@example.com"
                self.user.is_enabled = True
                self.user.save()
                self.user.proxies.set([self.proxy])
                CaptiveRateLimit.objects.all().delete()
                self.authenticate()
                CaptiveRateLimit.objects.all().delete()
                pending_response, delivery = self.send()
                pending = CaptiveOTP.objects.get(challenge_hash=digest(pending_response.context["challenge"]))
                setattr(self.user, field, value)
                self.user.save()
                pending.refresh_from_db()
                self.assertIsNotNone(pending.consumed_at)
                self.assertFalse(CaptiveSession.objects.filter(user=self.user, revoked=False).exists())
                rejected = self.client.post("/_captive/verify-otp/", {
                    "challenge": pending_response.context["challenge"],
                    "otp": delivery.call_args.args[1],
                    "identifier": old_identifier,
                })
                self.assertEqual(rejected.status_code, 200)
                self.assertFalse(CaptiveSession.objects.filter(user=self.user, revoked=False).exists())

    def test_csrf_enforced(self):
        client = Client(enforce_csrf_checks=True, HTTP_HOST=self.proxy.domain_name,
                        HTTP_X_CAPTIVE_KEY=route_key(self.proxy.domain_name), HTTP_X_CAPTIVE_IP="192.0.2.10")
        self.assertEqual(client.post("/_captive/send-otp/", {"identifier": "9999999999"}).status_code, 403)
        response = client.get("/_captive/login/")
        token = response.cookies["csrftoken"].value
        self.assertTrue(response.cookies["csrftoken"]["secure"])
        with patch("proxies.captive.deliver"):
            response = client.post("/_captive/send-otp/", {"identifier": "9999999999", "csrfmiddlewaretoken": token}, HTTP_ORIGIN="https://app.example.com")
        self.assertEqual(response.status_code, 200)

    def test_open_redirects_and_captive_loops(self):
        for bad in ("https://evil.test/", "//evil.test/", "/\\evil.test", "/%2fevil.test", "/%252fevil.test", "/\n/evil", "/_captive/login/", "/%5cevil.test", "javascript:alert(1)"):
            self.assertEqual(local_next(bad), "/", bad)
        self.assertEqual(local_next("/a?x=1&y=a%20b"), "/a?x=1&y=a%20b")

    def test_registration_validation(self):
        for contacts in ({"mobile_number": "8765432109", "email_address": "new@example.com"},):
            form = AuthorizedUserForm({"name": "Name", "rank": "Rank", "section": "IT", **contacts})
            self.assertTrue(form.is_valid(), form.errors)
        for contacts in ({}, {"mobile_number": "9999999999"}, {"email_address": "new@example.com"},
                         {"mobile_number": "invalid", "email_address": "new@example.com"},
                         {"mobile_number": "9999999999", "email_address": "broken"}):
            form = AuthorizedUserForm({"name": "Name", "rank": "Rank", "section": "IT", **contacts})
            self.assertFalse(form.is_valid(), contacts)
        duplicate_mobile = AuthorizedUserForm({"name": "Other", "rank": "Rank", "section": "IT",
            "mobile_number": "+91 83358 52826", "email_address": "other@example.com"})
        duplicate_email = AuthorizedUserForm({"name": "Other", "rank": "Rank", "section": "IT",
            "mobile_number": "8765432109", "email_address": "TEST@EXAMPLE.COM"})
        self.assertFalse(duplicate_mobile.is_valid())
        self.assertFalse(duplicate_email.is_valid())

    def test_admin_permissions_pages_and_confirmation(self):
        operator = get_user_model().objects.create_user("limited", is_staff=True)
        console = Client()
        console.force_login(operator)
        routes = [("captive_users", [], "view"), ("captive_user_add", [], "add"),
                  ("captive_user_edit", [self.user.pk], "change"), ("captive_user_action", [self.user.pk, "toggle"], "toggle"),
                  ("captive_user_action", [self.user.pk, "assign"], "assign"), ("captive_user_action", [self.user.pk, "delete"], "delete")]
        for name, args, perm in routes:
            url = reverse(name, args=args)
            self.assertEqual(console.get(url).status_code, 403)
            self.assertEqual(console.post(url, {}).status_code, 403)
        console.force_login(self.admin)
        for name, args, perm in routes:
            self.assertEqual(console.get(reverse(name, args=args)).status_code, 200)
        self.assertEqual(console.get(reverse("captive_audit")).status_code, 200)
        delete_url = reverse("captive_user_action", args=[self.user.pk, "delete"])
        self.assertEqual(console.post(delete_url).status_code, 403)
        self.assertEqual(console.post(delete_url, {"confirm": self.user.pk}).status_code, 302)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.deleted_at)
        self.assertIsNone(self.user.mobile_number)
        self.assertTrue(CaptiveAudit.objects.filter(action="user_deleted", actor=self.admin).exists())

    def test_change_permission_cannot_escalate_assignments_or_enable(self):
        operator = get_user_model().objects.create_user("editor", is_staff=True)
        operator.user_permissions.add(Permission.objects.get(codename="change_captiveportaluser"))
        self.user.is_enabled = False
        self.user.save()
        console = Client()
        console.force_login(operator)
        response = console.post(reverse("captive_user_edit", args=[self.user.pk]), {
            "name": "Edited", "rank": "Officer", "section": "IT", "mobile_number": "9876543210",
            "email_address": "test@example.com",
            "is_enabled": "on", "all_fqdns": "on", "proxies": [self.other.pk]})
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_enabled)
        self.assertFalse(self.user.all_fqdns)
        self.assertEqual(list(self.user.proxies.all()), [self.proxy])

    @patch("proxies.forms.resolve_public_ip", return_value=None)
    def test_proxy_checkbox_and_https_validation(self, resolver):
        console = Client()
        console.force_login(self.admin)
        response = console.get(reverse("proxy_edit", args=[self.proxy.pk]))
        self.assertContains(response, 'name="captive_portal_enabled"')
        self.assertTrue(response.context["form"]["captive_portal_enabled"].value())
        data = {"domain_name": self.proxy.domain_name, "backend_private_ip": "10.0.0.1", "backend_port": 8080,
                "incoming_protocol": "https", "backend_protocol": "http", "certificate_bundle": self.cert.pk, "enabled": "on"}
        response = console.post(reverse("proxy_edit", args=[self.proxy.pk]), data)
        self.assertEqual(response.status_code, 302)
        self.proxy.refresh_from_db()
        self.assertFalse(self.proxy.captive_portal_enabled)
        data.update(captive_portal_enabled="on", incoming_protocol="http")
        response = console.post(reverse("proxy_edit", args=[self.proxy.pk]), data)
        self.assertContains(response, "requires HTTPS")

    def test_real_destination_and_ip_rate_policies(self):
        with override_settings(CAPTIVE_DESTINATION_SEND_LIMIT=1, CAPTIVE_RESEND_SECONDS=0):
            self.send()
            _, delivery = self.send()
            self.assertFalse(delivery.called)
        CaptiveRateLimit.objects.all().delete()
        with override_settings(CAPTIVE_IP_SEND_LIMIT=1):
            self.send("8765432109")  # Unknown destinations consume the IP budget too.
            _, delivery = self.send()
            self.assertFalse(delivery.called)
        CaptiveRateLimit.objects.all().delete()
        with override_settings(CAPTIVE_USER_SEND_LIMIT=1):
            self.send()
            _, delivery = self.send("TEST@example.com")
            self.assertFalse(delivery.called)
        CaptiveRateLimit.objects.all().delete()
        with override_settings(CAPTIVE_FQDN_SEND_LIMIT=1):
            self.send("8765432109")
            _, delivery = self.send()
            self.assertFalse(delivery.called)

    def test_session_expiry_and_fixation(self):
        self.client.cookies[COOKIE] = "attacker-controlled"
        self.authenticate()
        self.assertNotEqual(self.client.cookies[COOKIE].value, "attacker-controlled")
        CaptiveSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.get("/_captive/status/").status_code, 401)


class SMSProviderTests(TestCase):
    @override_settings(DEBUG=False)
    def test_development_backend_cannot_run_in_production(self):
        from django.core.exceptions import ImproperlyConfigured
        from .captive_sms import DevelopmentSMS
        with self.assertRaises(ImproperlyConfigured):
            DevelopmentSMS().send("+919876543210", "123456")

    @override_settings(CAPTIVE_SMS_API_URL="https://sms.example.test/send", CAPTIVE_SMS_HTTP_ADAPTER="proxies.captive_sms.GatewayAdapter")
    def test_unconfigured_adapter_fails_closed(self):
        from django.core.exceptions import ImproperlyConfigured
        from .captive_sms import HTTPSMS
        with self.assertRaises(ImproperlyConfigured):
            HTTPSMS().send("+919876543210", "123456")

    @override_settings(CAPTIVE_SMS_API_URL="https://sms.example.test/send", CAPTIVE_SMS_API_TOKEN="test-token")
    def test_transport_uses_adapter_and_both_timeouts(self):
        from .captive_sms import HTTPSMS
        with patch("proxies.captive_sms.import_string") as load, patch("proxies.captive_sms.http.client.HTTPSConnection") as connection:
            adapter = load.return_value.return_value
            adapter.build_request.return_value = ("POST", "/send", {"Content-Type": "application/octet-stream"}, b"adapter payload")
            adapter.accepted.return_value = True
            connection.return_value.getresponse.return_value.read.return_value = b"accepted"
            HTTPSMS().send("+919876543210", "123456")
            connection.assert_called_once_with("sms.example.test", None, timeout=5)
            connection.return_value.sock.settimeout.assert_called_once_with(10)
            connection.return_value.request.assert_called_once_with("POST", "/send", body=b"adapter payload", headers={"Content-Type": "application/octet-stream"})
            connection.return_value.close.assert_called_once()

    @override_settings(CAPTIVE_SMS_BACKEND="development", DEBUG=True)
    def test_delivery_is_sms_only_and_development_provider_does_not_contact_gateway(self):
        from .captive_sms import deliver
        with patch("proxies.captive_sms.DevelopmentSMS.send") as send:
            deliver("+919999999999", "123456")
        send.assert_called_once_with("+919999999999", "123456")
