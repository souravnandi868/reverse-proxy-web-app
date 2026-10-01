from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class CaptiveMigrationTests(TransactionTestCase):
    def test_upgrade_preserves_existing_proxy_and_reverse_migration(self):
        before = [("proxies", "0012_traffic_domain_time")]
        captive = [("proxies", "0013_captive_portal")]
        after = [("proxies", "0014_captive_sms_identifier")]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        try:
            apps = executor.loader.project_state(before).apps
            admin = apps.get_model("auth", "User").objects.create(username="migration-admin")
            proxy = apps.get_model("proxies", "ProxyConfig").objects.create(domain_name="existing.example.com",
                backend_private_ip="10.0.0.10", backend_port=8000, incoming_protocol="http",
                websocket_enabled=True, created_by=admin, updated_by=admin)
            executor = MigrationExecutor(connection)
            executor.migrate(captive)
            apps = executor.loader.project_state(captive).apps
            legacy_user = apps.get_model("proxies", "CaptivePortalUser").objects.create(
                name="Legacy user", section="IT", rank="Officer", mobile_number="9999999999")
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            apps = executor.loader.project_state(after).apps
            migrated = apps.get_model("proxies", "ProxyConfig").objects.get(pk=proxy.pk)
            self.assertFalse(migrated.captive_portal_enabled)
            self.assertTrue(migrated.websocket_enabled)
            self.assertEqual(migrated.backend_private_ip, "10.0.0.10")
            self.assertEqual(apps.get_model("proxies", "CaptiveSession").objects.count(), 0)
            self.assertFalse(apps.get_model("proxies", "CaptivePortalUser").objects.get(pk=legacy_user.pk).is_enabled)
            MigrationExecutor(connection).migrate(before)
            self.assertEqual(apps.get_model("proxies", "ProxyConfig").objects.values_list("domain_name", flat=True).get(), "existing.example.com")
        finally:
            MigrationExecutor(connection).migrate(after)
