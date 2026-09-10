from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from datetime import date
from unittest.mock import patch

from .forms import CertificateBundleForm, ProxyConfigForm
from .models import ProxyConfig
from .services import OperationResult, render_proxy_config


class ProxyValidationTests(TestCase):
    def test_domain_is_strictly_validated(self):
        form = ProxyConfigForm(data={"domain_name": "bad_domain", "backend_private_ip": "10.0.0.4", "backend_port": 8080, "incoming_protocol": "https", "backend_protocol": "http", "nat_notes": "", "firewall_notes": "", "enabled": True})
        self.assertFalse(form.is_valid())
        self.assertIn("domain_name", form.errors)

    def test_fully_qualified_domain_is_accepted(self):
        form = ProxyConfigForm(data={"domain_name": "app.example.com", "backend_private_ip": "10.0.0.4", "backend_port": 8080, "incoming_protocol": "http", "backend_protocol": "http", "nat_notes": "", "firewall_notes": "", "enabled": True})
        self.assertTrue(form.is_valid(), form.errors)

    @patch("proxies.forms.resolve_public_ip", return_value="203.0.113.10")
    def test_public_ip_is_resolved_when_blank(self, resolve_public_ip):
        form = ProxyConfigForm(data={"domain_name": "app.example.com", "backend_private_ip": "10.0.0.4", "backend_port": 8080, "incoming_protocol": "http", "backend_protocol": "http", "nat_notes": "", "firewall_notes": "", "enabled": True})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["public_ip"], "203.0.113.10")
        resolve_public_ip.assert_called_once_with("app.example.com")

    @patch("proxies.forms.resolve_public_ip", return_value="203.0.113.10")
    def test_manual_public_ip_is_preserved(self, resolve_public_ip):
        form = ProxyConfigForm(data={"domain_name": "app.example.com", "public_ip": "198.51.100.20", "backend_private_ip": "10.0.0.4", "backend_port": 8080, "incoming_protocol": "http", "backend_protocol": "http", "nat_notes": "", "firewall_notes": "", "enabled": True})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["public_ip"], "198.51.100.20")
        resolve_public_ip.assert_not_called()

    def test_https_requires_certificate(self):
        form = ProxyConfigForm(data={"domain_name": "app.example.com", "backend_private_ip": "10.0.0.4", "backend_port": 8080, "incoming_protocol": "https", "backend_protocol": "http", "nat_notes": "", "firewall_notes": "", "enabled": True})
        self.assertFalse(form.is_valid())
        self.assertIn("certificate_bundle", form.errors)

    @patch("proxies.forms.certificate_valid_until", return_value=date(2030, 12, 31))
    def test_certificate_valid_until_is_extracted_automatically(self, certificate_valid_until):
        form = CertificateBundleForm(data={"name": "wildcard-example-com"}, files={
            "certificate": SimpleUploadedFile("certificate.pem", b"-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----"),
            "private_key": SimpleUploadedFile("private.key", b"-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----"),
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["valid_until"], date(2030, 12, 31))
        certificate_valid_until.assert_called_once()

    def test_rendered_config_uses_fixed_directive_shape(self):
        user = get_user_model().objects.create_user("operator", password="correct horse battery staple", is_staff=True)
        proxy = ProxyConfig.objects.create(domain_name="app.example.com", backend_private_ip="10.0.0.4", backend_port=8080, incoming_protocol="https", backend_protocol="https", created_by=user, updated_by=user)
        rendered = render_proxy_config(proxy)
        self.assertIn("proxy_pass https://10.0.0.4:8080;", rendered)
        self.assertIn("proxy_ssl_server_name on;", rendered)
        self.assertNotIn("subprocess", rendered)

    def test_non_staff_cannot_access_dashboard(self):
        user = get_user_model().objects.create_user("viewer", password="correct horse battery staple")
        self.client.force_login(user)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 403)

    def test_staff_can_render_dashboard(self):
        user = get_user_model().objects.create_user("operator", password="correct horse battery staple", is_staff=True)
        self.client.force_login(user)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Proxy configurations")

    @patch("proxies.views.apply_proxy")
    def test_disabling_proxy_applies_disabled_state(self, apply_proxy):
        user = get_user_model().objects.create_user("operator", password="correct horse battery staple", is_staff=True)
        proxy = ProxyConfig.objects.create(domain_name="app.example.com", backend_private_ip="10.0.0.4", backend_port=8080, incoming_protocol="http", backend_protocol="http", created_by=user, updated_by=user)
        apply_proxy.return_value = OperationResult(True, "NGINX configuration validated and reloaded.")
        self.client.force_login(user)

        response = self.client.post(f"/proxies/{proxy.pk}/toggle/")

        self.assertRedirects(response, "/")
        proxy.refresh_from_db()
        self.assertFalse(proxy.enabled)
        apply_proxy.assert_called_once()
        self.assertFalse(apply_proxy.call_args.args[0].enabled)

    def test_staff_can_log_out_with_post(self):
        user = get_user_model().objects.create_user("logout-user", password="correct horse battery staple", is_staff=True)
        self.client.force_login(user)
        response = self.client.post("/logout/")
        self.assertRedirects(response, "/login/")
        self.assertNotIn("_auth_user_id", self.client.session)
