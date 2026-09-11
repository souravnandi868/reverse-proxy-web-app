from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test import override_settings
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from .forms import CertificateBundleForm, ProxyConfigForm
from .models import AuditLog, ProxyConfig
from .services import OperationResult, recent_traffic_logs, render_proxy_config


class ProxyValidationTests(TestCase):
    def test_sidebar_destinations_have_distinct_content(self):
        user = get_user_model().objects.create_user("navigation-operator", is_staff=True)
        self.client.force_login(user)
        dashboard = self.client.get("/")
        self.assertContains(dashboard, 'href="/proxies/"')
        self.assertContains(dashboard, 'href="/domains/"')
        routes = self.client.get("/proxies/")
        self.assertTemplateUsed(routes, "proxies/proxy_list.html")
        self.assertContains(routes, "Add Reverse Proxy")
        self.assertNotContains(routes, 'class="stats"')
        domains = self.client.get("/domains/")
        self.assertTemplateUsed(domains, "proxies/domains.html")
        self.assertContains(domains, "Resolved public IP")
        self.assertNotContains(domains, "<th>Actions</th>")

    def test_new_navigation_pages_require_staff(self):
        for path in ("/proxies/", "/domains/"):
            self.assertEqual(self.client.get(path).status_code, 302)
        user = get_user_model().objects.create_user("navigation-viewer")
        self.client.force_login(user)
        for path in ("/proxies/", "/domains/"):
            self.assertEqual(self.client.get(path).status_code, 403)

    @patch("proxies.forms.resolve_public_ip", return_value="8.8.8.8")
    def test_manual_public_ip_is_ignored_on_save(self, resolver):
        user = get_user_model().objects.create_user("dns-operator", is_staff=True)
        self.client.force_login(user)
        response = self.client.post("/proxies/new/", {
            "domain_name": "app.example.com", "public_ip": "1.1.1.1",
            "backend_private_ip": "10.0.0.4", "backend_port": 8080,
            "incoming_protocol": "http", "backend_protocol": "http", "enabled": True,
        })
        self.assertRedirects(response, "/")
        self.assertEqual(ProxyConfig.objects.get().public_ip, "8.8.8.8")

    def test_admin_public_ip_is_read_only(self):
        user = get_user_model().objects.create_superuser("dns-admin", password="test-password")
        self.client.force_login(user)
        response = self.client.get("/admin/proxies/proxyconfig/add/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="public_ip"')
        self.assertContains(response, "resolved automatically")

    @patch("proxies.views.recent_traffic_logs")
    def test_traffic_page_shows_connections_not_configuration_events(self, logs):
        user = get_user_model().objects.create_user("traffic-operator", is_staff=True)
        AuditLog.objects.create(actor=user, action="create", target="configuration-event.example.com")
        logs.return_value = [{"source_ip": "198.51.100.8", "destination_fqdn": "app.example.com",
                              "destination_server": "10.0.0.4:8080", "status": 200}]
        self.client.force_login(user)
        response = self.client.get("/audit/")
        self.assertContains(response, "198.51.100.8")
        self.assertContains(response, "10.0.0.4:8080")
        self.assertNotContains(response, "configuration-event.example.com")

    def test_domain_is_strictly_validated(self):
        form = ProxyConfigForm(data={"domain_name": "bad_domain", "backend_private_ip": "10.0.0.4", "backend_port": 8080, "incoming_protocol": "https", "backend_protocol": "http", "nat_notes": "", "firewall_notes": "", "enabled": True})
        self.assertFalse(form.is_valid())
        self.assertIn("domain_name", form.errors)

    @patch("proxies.forms.resolve_public_ip", return_value="203.0.113.10")
    def test_fully_qualified_domain_is_accepted(self, resolve_public_ip):
        form = ProxyConfigForm(data={"domain_name": "app.example.com", "backend_private_ip": "10.0.0.4", "backend_port": 8080, "incoming_protocol": "http", "backend_protocol": "http", "nat_notes": "", "firewall_notes": "", "enabled": True})
        self.assertTrue(form.is_valid(), form.errors)

    @patch("proxies.forms.resolve_public_ip", return_value="203.0.113.10")
    def test_public_ip_is_resolved_when_blank(self, resolve_public_ip):
        form = ProxyConfigForm(data={"domain_name": "app.example.com", "backend_private_ip": "10.0.0.4", "backend_port": 8080, "incoming_protocol": "http", "backend_protocol": "http", "nat_notes": "", "firewall_notes": "", "enabled": True})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["public_ip"], "203.0.113.10")
        resolve_public_ip.assert_called_once_with("app.example.com")

    @patch("proxies.forms.resolve_public_ip", return_value="203.0.113.10")
    def test_public_ip_is_always_resolved_from_domain(self, resolve_public_ip):
        form = ProxyConfigForm(data={"domain_name": "app.example.com", "backend_private_ip": "10.0.0.4", "backend_port": 8080, "incoming_protocol": "http", "backend_protocol": "http", "nat_notes": "", "firewall_notes": "", "enabled": True})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["public_ip"], "203.0.113.10")
        resolve_public_ip.assert_called_once_with("app.example.com")
        self.assertNotIn("public_ip", form.fields)

    @patch("proxies.forms.resolve_public_ip", return_value=None)
    def test_public_ip_requires_fqdn_resolution(self, resolve_public_ip):
        form = ProxyConfigForm(data={"domain_name": "app.example.com", "backend_private_ip": "10.0.0.4", "backend_port": 8080, "incoming_protocol": "http", "backend_protocol": "http", "nat_notes": "", "firewall_notes": "", "enabled": True})
        self.assertFalse(form.is_valid())
        self.assertIn("domain_name", form.errors)

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
        self.assertIn("access_log /var/log/nginx/proxy-admin/app.example.com.access.log proxy_admin;", rendered)
        self.assertNotIn("subprocess", rendered)

    def test_recent_traffic_logs_reads_source_and_destination(self):
        with TemporaryDirectory() as log_dir:
            Path(log_dir, "app.example.com.access.log").write_text(
                '{"time":"2026-09-11T10:00:00+00:00","source_ip":"198.51.100.8","destination_fqdn":"app.example.com","destination_server":"10.0.0.4:8080","request":"GET /health HTTP/1.1","status":200,"bytes":12,"request_time":0.004,"user_agent":"Test"}\n',
                encoding="utf-8",
            )
            with override_settings(NGINX_ACCESS_LOG_DIR=log_dir):
                logs = recent_traffic_logs()
        self.assertEqual(logs[0]["source_ip"], "198.51.100.8")
        self.assertEqual(logs[0]["destination_server"], "10.0.0.4:8080")

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
