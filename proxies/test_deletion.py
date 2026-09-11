"""Deletion integration tests use real temporary files, never a running Nginx."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, DatabaseError
from django.test import Client, TransactionTestCase, override_settings

from .models import AuditLog, CertificateBundle, ConfigurationBackup, ProxyConfig
from .services import OperationResult, _request

spec = importlib.util.spec_from_file_location("nginx_ops", Path(__file__).resolve().parent.parent / "ops/nginx-ops.py")
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


class ProxyDeletionTests(TransactionTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        media = override_settings(MEDIA_ROOT=self.root)
        media.enable()
        self.addCleanup(media.disable)
        self.managed = self.root / "managed"
        self.managed.mkdir()
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.admin = get_user_model().objects.create_superuser("delete-admin", password="test-password")
        self.client.force_login(self.admin)
        self.cert = self.root / "certificate.pem"
        self.key = self.root / "private.pem"
        self.cert.write_bytes(b"certificate")
        self.key.write_bytes(b"SECRET PRIVATE KEY")
        self.bundle = CertificateBundle.objects.create(name="shared", certificate=self.cert.name, private_key=self.key.name, uploaded_by=self.admin)
        self.proxy = ProxyConfig.objects.create(domain_name="app.example.com", public_ip="203.0.113.1", backend_private_ip="10.0.0.4", backend_port=8080, incoming_protocol="https", backend_protocol="http", certificate_bundle=self.bundle, created_by=self.admin, updated_by=self.admin, nat_notes="SECRET TOKEN")
        self.other = ProxyConfig.objects.create(domain_name="other.example.com", backend_private_ip="10.0.0.5", backend_port=80, certificate_bundle=self.bundle, created_by=self.admin, updated_by=self.admin)
        self.backup = ConfigurationBackup.objects.create(proxy=self.proxy, version=1, rendered_config="old config", created_by=self.admin, reason="before update")
        self.route = self.managed / "app.example.com.conf"
        self.route.write_bytes(b"original managed route")
        self.other_route = self.managed / "other.example.com.conf"
        self.other_route.write_bytes(b"other route")
        for name in ("app.example.com.access.log", "app.example.com.error.log"):
            (self.logs / name).write_bytes(b"retained logs")
        self.url = f"/proxies/{self.proxy.pk}/delete/"
        for name, value in (("NGINX_DIR", self.managed), ("LOG_DIR", self.logs)):
            patcher = patch.object(ops, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def helper(self, action, payload):
        self.assertFalse(connection.in_atomic_block)
        self.assertEqual(action, "apply")
        self.assertIs(payload["enabled"], False)
        self.assertTrue(ProxyConfig.objects.filter(pk=self.proxy.pk).exists())
        self.assertTrue(ConfigurationBackup.objects.filter(pk=self.backup.pk).exists())
        self.assertFalse(AuditLog.objects.filter(action="delete").exists())
        self.assertEqual(ProxyConfig.objects.get(pk=self.proxy.pk).enabled, self.proxy.enabled)
        return OperationResult(*ops.handle({"action": action, "payload": payload}))

    def assert_retained(self):
        saved = ProxyConfig.objects.get(pk=self.proxy.pk)
        self.assertEqual(saved.enabled, self.proxy.enabled)
        self.assertTrue(ConfigurationBackup.objects.filter(pk=self.backup.pk).exists())
        self.assertFalse(AuditLog.objects.filter(action="delete").exists())

    def test_get_confirms_without_mutation_and_other_methods_rejected(self):
        with patch("proxies.views.apply_proxy") as helper:
            response = self.client.get(self.url)
            self.assertContains(response, self.proxy.domain_name)
            self.assertContains(response, "http://10.0.0.4:8080")
            self.assertContains(response, "active Nginx route will be removed")
            for method in ("put", "patch", "delete", "head"):
                self.assertEqual(getattr(self.client, method)(self.url).status_code, 405)
            helper.assert_not_called()
        self.assert_retained()
        self.assertTrue(self.route.exists())

    def test_authentication_superuser_and_csrf(self):
        with patch("proxies.views.apply_proxy") as helper:
            self.client.logout()
            self.assertEqual(self.client.post(self.url).status_code, 302)
            staff = get_user_model().objects.create_user("staff", is_staff=True)
            self.client.force_login(staff)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.client.post(self.url).status_code, 403)
            csrf = Client(enforce_csrf_checks=True)
            csrf.force_login(self.admin)
            self.assertEqual(csrf.post(self.url).status_code, 403)
            csrf.get(self.url)
            helper.return_value = OperationResult(False, "unavailable")
            self.assertEqual(csrf.post(self.url, {"csrfmiddlewaretoken": csrf.cookies["csrftoken"].value}).status_code, 302)
            helper.assert_called_once()
        self.assert_retained()

    def test_success_enabled_and_disabled_safe_audit_cascade_and_retained_files(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                self.proxy.enabled = enabled
                self.proxy.save()
                original_pk = self.proxy.pk
                with patch("proxies.services._request", side_effect=self.helper), patch.object(ops.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as command:
                    self.assertEqual(self.client.post(self.url).status_code, 302)
                self.assertFalse(ProxyConfig.objects.filter(pk=original_pk).exists())
                self.assertFalse(self.route.exists())
                self.assertFalse(ConfigurationBackup.objects.filter(pk=self.backup.pk).exists())
                event = AuditLog.objects.get(action="delete")
                self.assertEqual(event.target, "app.example.com")
                self.assertEqual(event.actor, self.admin)
                self.assertEqual(event.detail, {"domain_name": "app.example.com", "public_ip": "203.0.113.1", "backend_private_ip": "10.0.0.4", "backend_port": 8080, "incoming_protocol": "https", "backend_protocol": "http", "enabled": enabled})
                self.assertNotIn("SECRET", json.dumps(event.detail))
                self.assertTrue(CertificateBundle.objects.filter(pk=self.bundle.pk).exists())
                self.assertEqual(ProxyConfig.objects.get(pk=self.other.pk).certificate_bundle_id, self.bundle.pk)
                self.assertEqual(self.cert.read_bytes(), b"certificate")
                self.assertEqual(self.key.read_bytes(), b"SECRET PRIVATE KEY")
                self.assertEqual(self.other_route.read_bytes(), b"other route")
                self.assertEqual(len(list(self.logs.iterdir())), 2)
                self.assertEqual([call.args[0] for call in command.call_args_list], [["/usr/sbin/nginx", "-t"], ["/usr/bin/systemctl", "reload", "nginx"]])
                event.delete()
                self.proxy.pk = original_pk
                self.proxy.save(force_insert=True)
                self.backup.pk = None
                self.backup.save(force_insert=True)
                self.route.write_bytes(b"original managed route")

    def test_helper_validation_reload_and_exception_failures_restore_original_state(self):
        for enabled in (True, False):
            self.proxy.enabled = enabled
            self.proxy.save()
            for results in ([SimpleNamespace(returncode=1)], [SimpleNamespace(returncode=0), SimpleNamespace(returncode=1)], [OSError("SECRET internal path")], [SimpleNamespace(returncode=0), TimeoutError("SECRET")]):
                with self.subTest(enabled=enabled, results=results):
                    with patch("proxies.services._request", side_effect=self.helper), patch.object(ops.subprocess, "run", side_effect=results) as command:
                        response = self.client.post(self.url, follow=True)
                    self.assertEqual(command.call_count, len(results))
                    self.assertNotContains(response, "SECRET")
                    self.assertContains(response, "Proxy retained")
                    self.assert_retained()
                    self.assertEqual(self.route.read_bytes(), b"original managed route")

    def test_unavailable_and_unexpected_errors_retain_state(self):
        for enabled in (True, False):
            self.proxy.enabled = enabled
            self.proxy.save()
            with patch("proxies.services.SOCKET_PATH", self.root / "missing.sock"):
                self.client.post(self.url)
            self.assert_retained()
            with patch("proxies.views.apply_proxy", side_effect=RuntimeError("SECRET")):
                response = self.client.post(self.url, follow=True)
            self.assertNotContains(response, "SECRET")
            self.assert_retained()

    def test_audit_failure_rolls_back_database_deletion(self):
        with patch("proxies.services._request", side_effect=self.helper), patch.object(ops.subprocess, "run", return_value=SimpleNamespace(returncode=0)), patch("proxies.views.log_action", side_effect=DatabaseError("SECRET")):
            response = self.client.post(self.url, follow=True)
        self.assertContains(response, "route may already be removed")
        self.assertNotContains(response, "SECRET")
        self.assert_retained()

    def test_disabled_absent_route_waits_for_reload_before_deletion(self):
        self.proxy.enabled = False
        self.proxy.save()
        self.route.unlink()

        def command(*args, **kwargs):
            self.assertFalse(self.route.exists())
            self.assert_retained()
            self.assertFalse(connection.in_atomic_block)
            return SimpleNamespace(returncode=0)

        with patch("proxies.services._request", side_effect=self.helper), patch.object(ops.subprocess, "run", side_effect=command) as run:
            self.client.post(self.url)
        self.assertEqual(run.call_count, 2)
        self.assertFalse(ProxyConfig.objects.filter(pk=self.proxy.pk).exists())

    def test_file_restoration_failure_keeps_database_record(self):
        with patch("proxies.services._request", side_effect=self.helper), patch.object(ops.subprocess, "run", return_value=SimpleNamespace(returncode=1)), patch.object(ops, "write_config", side_effect=OSError("SECRET")):
            response = self.client.post(self.url, follow=True)
        self.assertNotContains(response, "SECRET")
        self.assertContains(response, "Proxy retained")
        self.assert_retained()

    def test_concurrent_edit_prevents_database_deletion(self):
        def modified(proxy):
            ProxyConfig.objects.filter(pk=proxy.pk).update(backend_port=9090)
            return OperationResult(True, "success")
        with patch("proxies.views.apply_proxy", side_effect=modified):
            self.client.post(self.url)
        self.assert_retained()
        self.assertEqual(ProxyConfig.objects.get(pk=self.proxy.pk).backend_port, 9090)

    def test_admin_cannot_bypass_safe_deletion(self):
        self.assertEqual(self.client.post(f"/admin/proxies/proxyconfig/{self.proxy.pk}/delete/", {"post": "yes"}).status_code, 403)
        self.assert_retained()

    def test_helper_rejects_unsafe_paths(self):
        for domain in ("../outside", "/absolute", "..", r"..\outside", "app.example.com\n"):
            with self.subTest(domain=domain), patch.object(ops.subprocess, "run") as command:
                ok, _ = ops.handle({"action": "apply", "payload": {"domain": domain, "config": "# Managed by NGINX Proxy Admin.", "enabled": False}})
                self.assertFalse(ok)
                command.assert_not_called()
        self.assertTrue(self.route.exists())

    def test_service_rejects_non_boolean_success(self):
        with patch("proxies.services.socket.AF_UNIX", 1, create=True), patch("proxies.services.SOCKET_PATH") as path, patch("proxies.services.socket.socket") as sock:
            path.exists.return_value = True
            for response in ({"ok": "false"}, {"ok": 1}, [], {}):
                sock.return_value.__enter__.return_value.recv.return_value = json.dumps(response).encode()
                self.assertFalse(_request("apply", {}).ok)
