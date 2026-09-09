from django.contrib.auth import get_user_model
from django.test import TestCase
from .forms import ProxyConfigForm
from .models import ProxyConfig
from .services import render_proxy_config


class ProxyValidationTests(TestCase):
    def test_domain_is_strictly_validated(self):
        form = ProxyConfigForm(data={"domain_name": "bad_domain", "backend_private_ip": "10.0.0.4", "backend_port": 8080, "incoming_protocol": "https", "backend_protocol": "http", "nat_notes": "", "firewall_notes": "", "enabled": True})
        self.assertFalse(form.is_valid())
        self.assertIn("domain_name", form.errors)

    def test_fully_qualified_domain_is_accepted(self):
        form = ProxyConfigForm(data={"domain_name": "app.example.com", "backend_private_ip": "10.0.0.4", "backend_port": 8080, "incoming_protocol": "http", "backend_protocol": "http", "nat_notes": "", "firewall_notes": "", "enabled": True})
        self.assertTrue(form.is_valid(), form.errors)

    def test_https_requires_certificate(self):
        form = ProxyConfigForm(data={"domain_name": "app.example.com", "backend_private_ip": "10.0.0.4", "backend_port": 8080, "incoming_protocol": "https", "backend_protocol": "http", "nat_notes": "", "firewall_notes": "", "enabled": True})
        self.assertFalse(form.is_valid())
        self.assertIn("certificate_bundle", form.errors)

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

    def test_staff_can_log_out_with_post(self):
        user = get_user_model().objects.create_user("logout-user", password="correct horse battery staple", is_staff=True)
        self.client.force_login(user)
        response = self.client.post("/logout/")
        self.assertRedirects(response, "/login/")
        self.assertNotIn("_auth_user_id", self.client.session)
