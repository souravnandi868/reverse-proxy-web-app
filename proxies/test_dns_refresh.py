from io import StringIO
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from .models import ProxyConfig


class DnsRefreshTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("dns-refresh")
        self.proxy = ProxyConfig.objects.create(domain_name="app.example.com", public_ip="8.8.8.8",
            backend_private_ip="10.0.0.4", backend_port=80, enabled=False, created_by=user, updated_by=user)

    def run_refresh(self):
        output, errors = StringIO(), StringIO()
        call_command("refresh_proxy_metadata", "--all", "--dns-only", stdout=output, stderr=errors)
        return output.getvalue(), errors.getvalue()

    @patch("proxies.management.commands.refresh_proxy_metadata.resolve_public_ip", return_value="1.1.1.1")
    @patch("proxies.management.commands.refresh_proxy_metadata.certificate_valid_until")
    def test_refreshes_existing_disabled_route_without_changing_config_timestamp(self, certificate, dns):
        updated_at = self.proxy.updated_at
        self.run_refresh()
        self.proxy.refresh_from_db()
        self.assertEqual(self.proxy.public_ip, "1.1.1.1")
        self.assertEqual(self.proxy.updated_at, updated_at)
        dns.assert_called_once_with("app.example.com")
        certificate.assert_not_called()

    @patch("proxies.management.commands.refresh_proxy_metadata.resolve_public_ip", return_value=None)
    def test_failed_dns_keeps_last_known_ip(self, dns):
        _, errors = self.run_refresh()
        self.proxy.refresh_from_db()
        self.assertEqual(self.proxy.public_ip, "8.8.8.8")
        self.assertIn("keeping previous value", errors)

    @patch("proxies.management.commands.refresh_proxy_metadata.resolve_public_ip")
    def test_concurrent_rename_does_not_receive_old_domain_result(self, dns):
        def rename(domain):
            ProxyConfig.objects.filter(pk=self.proxy.pk).update(domain_name="new.example.com", public_ip="9.9.9.9")
            return "1.1.1.1"
        dns.side_effect = rename
        self.run_refresh()
        self.proxy.refresh_from_db()
        self.assertEqual(self.proxy.public_ip, "9.9.9.9")
