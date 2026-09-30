from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import ProxyConfig
from .services import render_proxy_config


class WebSocketProxyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("websocket-operator", is_staff=True)
        self.client.force_login(self.user)
        self.data = {
            "domain_name": "app.example.com", "backend_private_ip": "10.0.0.4",
            "backend_port": 8080, "incoming_protocol": "http",
            "backend_protocol": "http", "enabled": "on",
        }

    def make_proxy(self, **kwargs):
        return ProxyConfig.objects.create(
            **{**self.data, "enabled": True, **kwargs},
            created_by=self.user, updated_by=self.user,
        )

    def test_normal_route_preserves_previous_configuration(self):
        proxy = self.make_proxy()
        self.assertFalse(proxy.websocket_enabled)
        self.assertEqual(render_proxy_config(proxy), EXPECTED_NORMAL_CONFIG)

    def test_websocket_directives_apply_to_whole_domain(self):
        for protocol in ("http", "https"):
            with self.subTest(backend_protocol=protocol):
                proxy = ProxyConfig(**{**self.data, "backend_protocol": protocol}, websocket_enabled=True)
                rendered = render_proxy_config(proxy)
                self.assertEqual(rendered.count("location "), 1)
                location = rendered.split("    location / {\n", 1)[1].split("    }", 1)[0]
                for directive in (
                    "proxy_http_version 1.1;",
                    "proxy_set_header Upgrade $http_upgrade;",
                    'proxy_set_header Connection "upgrade";',
                    "proxy_read_timeout 3600s;",
                    "proxy_send_timeout 3600s;",
                    "proxy_buffering off;",
                ):
                    self.assertEqual(location.count(directive), 1)
                self.assertNotIn("proxy_read_timeout 60s;", rendered)
                self.assertNotIn("/websockify", rendered)
                self.assertIn(f"proxy_pass {protocol}://10.0.0.4:8080;", location)

    def test_authorization_passes_through_without_override(self):
        for enabled in (False, True):
            with self.subTest(websocket_enabled=enabled):
                proxy = ProxyConfig(**self.data, websocket_enabled=enabled)
                rendered = render_proxy_config(proxy)
                self.assertNotIn("proxy_pass_request_headers off", rendered)
                self.assertNotRegex(rendered, r"(?i)proxy_set_header\s+authorization\s")

    @patch("proxies.forms.resolve_public_ip", return_value="8.8.8.8")
    def test_create_and_edit_checkbox_persists_and_can_be_disabled(self, resolver):
        response = self.client.get("/proxies/new/")
        self.assertContains(response, "Enable WebSocket support")
        self.assertContains(response, 'type="checkbox" name="websocket_enabled"')
        self.assertRedirects(self.client.post("/proxies/new/", {
            **self.data, "websocket_enabled": "on",
        }), "/")
        proxy = ProxyConfig.objects.get()
        self.assertTrue(proxy.websocket_enabled)
        edit_url = f"/proxies/{proxy.pk}/edit/"
        response = self.client.get(edit_url)
        self.assertTrue(response.context["form"]["websocket_enabled"].value())
        self.assertRedirects(self.client.post(edit_url, self.data), "/")
        proxy.refresh_from_db()
        self.assertFalse(proxy.websocket_enabled)
        self.assertEqual(render_proxy_config(proxy), EXPECTED_NORMAL_CONFIG)
        self.assertRedirects(self.client.post(edit_url, {
            **self.data, "websocket_enabled": "on",
        }), "/")
        proxy.refresh_from_db()
        self.assertTrue(proxy.websocket_enabled)

    @patch("proxies.forms.resolve_public_ip", return_value="8.8.8.8")
    def test_create_without_checkbox_defaults_to_normal_route(self, resolver):
        self.assertRedirects(self.client.post("/proxies/new/", self.data), "/")
        proxy = ProxyConfig.objects.get()
        self.assertFalse(proxy.websocket_enabled)
        self.assertEqual(render_proxy_config(proxy), EXPECTED_NORMAL_CONFIG)


EXPECTED_NORMAL_CONFIG = """# Managed by NGINX Proxy Admin. Do not edit manually.
server {
    listen 80;
    server_name app.example.com;
    access_log /var/log/nginx/proxy-admin/app.example.com.access.log proxy_admin;
    error_log /var/log/nginx/proxy-admin/app.example.com.error.log warn;
    location / {
        proxy_pass http://10.0.0.4:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 5s;
        proxy_read_timeout 60s;
    }
}
"""
