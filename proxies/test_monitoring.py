import hashlib
import importlib.util
import json
import sys
from io import StringIO
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone
from .models import ProxyConfig, ServerMonitor


class MonitoringTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("monitor-user", is_staff=True)
        self.monitor = ServerMonitor.objects.create(address="10.0.0.4", is_reverse_proxy=True, token_hash=hashlib.sha256(b"test-token").hexdigest())
        self.sample = {"cpu": 25, "ram": {"used": 400, "total": 1000, "percent": 40},
                       "disks": [{"mount": "/var/log/nginx/", "used": 10, "total": 100, "free": 85, "percent": 10}],
                       "interfaces": [{"name": "eth0", "rx": 100, "tx": 50, "speed_mbps": 1000}]}

    def send(self, sample=None, token="test-token"):
        return Client(enforce_csrf_checks=True).post("/monitor/ingest/", json.dumps(self.sample if sample is None else sample),
            content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_MONITOR_ADDRESS="10.0.0.4")

    def test_ingestion_authenticates_without_browser_session(self):
        self.assertEqual(self.send(token="wrong").status_code, 401)
        self.monitor.refresh_from_db()
        self.assertIsNone(self.monitor.received_at)
        self.assertEqual(self.send().status_code, 200)
        self.monitor.refresh_from_db()
        self.assertEqual(self.monitor.latest, self.sample)
        self.assertIsNotNone(self.monitor.received_at)

    def test_invalid_samples_do_not_replace_measurements(self):
        self.send()
        for bad in (None, [], {"cpu": 12}, dict(self.sample, cpu=float("nan")), dict(self.sample, cpu=101), dict(self.sample, interfaces="bad")):
            response = Client().post("/monitor/ingest/", json.dumps(bad), content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token", HTTP_X_MONITOR_ADDRESS="10.0.0.4")
            self.assertEqual(response.status_code, 400)
        self.monitor.refresh_from_db()
        self.assertEqual(self.monitor.latest, self.sample)

    def test_snapshots_only_show_nginx_host_and_mark_stale(self):
        for domain in ("one.example.com", "two.example.com"):
            ProxyConfig.objects.create(domain_name=domain, backend_private_ip="10.0.0.99", backend_port=80,
                                       created_by=self.user, updated_by=self.user)
        self.client.force_login(self.user)
        data = self.client.get("/servers/metrics/").json()["servers"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["status"], "waiting")
        self.assertEqual(data[0]["address"], "10.0.0.4")
        self.assertNotIn("domains", data[0])
        self.send()
        self.assertEqual(self.client.get("/servers/metrics/").json()["servers"][0]["status"], "live")
        ServerMonitor.objects.update(received_at=timezone.now() - timedelta(seconds=31))
        response = self.client.get("/servers/metrics/")
        self.assertEqual(response.json()["servers"][0]["status"], "stale")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn("token_hash", response.content.decode())

    def test_resource_pages_require_staff(self):
        for path in ("/servers/", "/servers/metrics/"):
            self.assertEqual(self.client.get(path).status_code, 302)
        self.user.is_staff = False
        self.user.save()
        self.client.force_login(self.user)
        for path in ("/servers/", "/servers/metrics/"):
            self.assertEqual(self.client.get(path).status_code, 403)

    def test_old_backend_agent_is_excluded_and_rejected(self):
        self.monitor.is_reverse_proxy = False
        self.monitor.save()
        self.assertEqual(self.send().status_code, 401)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/servers/metrics/").json()["servers"], [])

    def test_enrollment_selects_only_new_nginx_host(self):
        output = StringIO()
        call_command("enroll_monitor", "10.0.0.10", stdout=output)
        self.monitor.refresh_from_db()
        self.assertFalse(self.monitor.is_reverse_proxy)
        self.assertEqual(ServerMonitor.objects.filter(is_reverse_proxy=True).get().address, "10.0.0.10")
        self.assertEqual(self.send().status_code, 401)
        self.assertIn("NGINX reverse proxy server", output.getvalue())

    def test_server_page_renders(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get("/servers/"), 'id="resource-monitor"')

    def test_agent_network_rates_handle_elapsed_time_and_counter_reset(self):
        spec = importlib.util.spec_from_file_location("monitor_agent", Path(__file__).resolve().parent.parent / "ops/monitor-agent.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"psutil": SimpleNamespace()}):
            spec.loader.exec_module(module)
        previous = {"eth0": SimpleNamespace(bytes_recv=100, bytes_sent=200)}
        current = {"eth0": SimpleNamespace(bytes_recv=1100, bytes_sent=100)}
        stats = {"eth0": SimpleNamespace(isup=True, speed=1000)}
        result = module.network_rates(previous, current, 5, stats)
        self.assertEqual(result[0]["rx"], 200)
        self.assertEqual(result[0]["tx"], 0)

    def test_agent_reads_only_nginx_log_filesystem(self):
        spec = importlib.util.spec_from_file_location("monitor_agent", Path(__file__).resolve().parent.parent / "ops/monitor-agent.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"psutil": SimpleNamespace()}):
            spec.loader.exec_module(module)
        with patch.object(module.psutil, "disk_usage", create=True) as usage:
            usage.return_value = SimpleNamespace(used=10, total=100, free=85, percent=10)
            self.assertEqual(module.nginx_storage(), self.sample["disks"])
            usage.assert_called_once_with("/var/log/nginx/")
            usage.side_effect = PermissionError()
            self.assertEqual(module.nginx_storage(), [])
