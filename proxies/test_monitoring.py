import hashlib
import importlib.util
import json
import os
import tempfile
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

    def test_dashboard_shows_latest_host_summary_and_stale_state(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get("/"), "Waiting for the NGINX host monitoring agent")
        sample = dict(self.sample, disks=[{"mount": "/var/log/nginx/", "kind": "directory", "used": 2048}],
                      nginx_rx_bps=1024, nginx_tx_bps=2048)
        self.assertEqual(self.send(sample).status_code, 200)
        response = self.client.get("/")
        self.assertContains(response, "Last received")
        self.assertRegex(response.content.decode(), r"Last received \d{2}:\d{2}:\d{2}")
        for value in ("10.0.0.4", "25.0%", "40.0%", "2.0 KiB", "1.00 KiB/s", "2.00 KiB/s"):
            self.assertContains(response, value)
        self.assertContains(response, 'value="25" aria-label="CPU usage 25.0%"')
        self.assertContains(response, 'value="40" aria-label="RAM usage 40.0%"')
        ServerMonitor.objects.update(received_at=timezone.now() - timedelta(seconds=31))
        response = self.client.get("/")
        self.assertContains(response, "Agent not reporting")
        self.assertContains(response, "25.0%")

    def test_dashboard_marks_missing_storage_and_traffic_unavailable(self):
        self.client.force_login(self.user)
        self.assertEqual(self.send().status_code, 200)
        response = self.client.get("/")
        self.assertContains(response, "NGINX log storage")
        self.assertContains(response, "Unavailable", count=3)

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

    def test_agent_measures_only_directory_files(self):
        spec = importlib.util.spec_from_file_location("monitor_agent", Path(__file__).resolve().parent.parent / "ops/monitor-agent.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"psutil": SimpleNamespace()}):
            spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "nginx"
            root.mkdir()
            expected = {"mount": str(root), "kind": "directory", "used": 0}
            self.assertEqual(module.nginx_storage(str(root)), [expected])
            (Path(folder) / "unrelated").write_bytes(b"x" * 1000)
            (root / "access.log").write_bytes(b"a" * 10)
            (root / "rotated").mkdir()
            (root / "rotated" / "access.log.1").write_bytes(b"b" * 25)
            os.link(root / "access.log", root / "access-hardlink.log")
            expected["used"] = 35
            self.assertEqual(module.nginx_storage(str(root)), [expected])
            with patch.object(module.os, "scandir", side_effect=PermissionError()):
                self.assertEqual(module.nginx_storage(str(root)), [])
            self.assertEqual(module.nginx_storage(str(root / "missing")), [])

    def test_directory_size_ingestion_including_empty_directory(self):
        for size in (0, 12345):
            self.sample["disks"] = [{"mount": "/var/log/nginx/", "kind": "directory", "used": size}]
            self.assertEqual(self.send().status_code, 200)
            self.monitor.refresh_from_db()
            self.assertEqual(self.monitor.latest, self.sample)
        self.sample["disks"][0]["used"] = -1
        self.assertEqual(self.send().status_code, 400)

    def test_interface_states_survive_ingestion(self):
        self.sample["interfaces"][0].update(is_up=False, rate_available=True)
        self.assertEqual(self.send().status_code, 200)
        self.monitor.refresh_from_db()
        self.assertEqual(self.monitor.latest, self.sample)
        self.sample["interfaces"][0]["is_up"] = "false"
        self.assertEqual(self.send().status_code, 400)

    def test_nginx_rates_are_validated_and_exposed_separately(self):
        self.client.force_login(self.user)
        for rx, tx in ((123.5, 456.5), (0, 0), (None, None)):
            self.sample.update(nginx_rx_bps=rx, nginx_tx_bps=tx)
            self.assertEqual(self.send().status_code, 200)
            metrics = self.client.get("/servers/metrics/").json()["servers"][0]["metrics"]
            self.assertEqual(metrics["nginx_rx_bps"], rx)
            self.assertEqual(metrics["nginx_tx_bps"], tx)
            self.assertEqual(metrics["interfaces"], self.sample["interfaces"])
        for bad in (-1, True, "100", float("inf")):
            self.sample.update(nginx_rx_bps=bad, nginx_tx_bps=0)
            self.assertEqual(self.send().status_code, 400)

    def test_agent_reports_down_new_and_unknown_interfaces(self):
        spec = importlib.util.spec_from_file_location("monitor_agent", Path(__file__).resolve().parent.parent / "ops/monitor-agent.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"psutil": SimpleNamespace()}):
            spec.loader.exec_module(module)
        counter = SimpleNamespace(bytes_recv=100, bytes_sent=200)
        current = {name: counter for name in ("eth0", "eth1", "tun0", "lo")}
        stats = {"eth0": SimpleNamespace(isup=False, speed=1000),
                 "eth1": SimpleNamespace(isup=True, speed=-1)}
        result = {n["name"]: n for n in module.network_rates({"eth0": counter}, current, 5, stats)}
        self.assertEqual(set(result), {"eth0", "eth1", "tun0"})
        self.assertFalse(result["eth0"]["is_up"])
        self.assertTrue(result["eth0"]["rate_available"])
        self.assertFalse(result["eth1"]["rate_available"])
        self.assertEqual(result["eth1"]["speed_mbps"], 0)
        self.assertIsNone(result["tun0"]["is_up"])
        self.assertFalse(module.network_rates(current, current, 0, stats)[0]["rate_available"])


class NginxTrafficTests(TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("monitor_agent", Path(__file__).resolve().parent.parent / "ops/monitor-agent.py")
        self.agent = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"psutil": SimpleNamespace()}):
            spec.loader.exec_module(self.agent)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.now = 0

    def reader(self):
        reader = self.agent.NginxTraffic(self.directory, clock=lambda: self.now)
        self.addCleanup(reader.close)
        return reader

    def append(self, name="one.access.log", rx=100, tx=200):
        with (self.directory / name).open("ab") as handle:
            handle.write(json.dumps({"received_bytes": rx, "sent_bytes": tx}).encode() + b"\n")

    def sample(self, reader, rx, tx, seconds=5):
        self.now += seconds
        self.assertEqual(reader.sample(), {"nginx_rx_bps": rx / seconds, "nginx_tx_bps": tx / seconds})

    def test_append_multiple_websites_and_restart_without_replay(self):
        self.append(rx=9000)
        reader = self.reader()
        self.append()
        self.append("two.access.log", 300, 400)
        self.append("error.log", 999, 999)
        self.sample(reader, 400, 600)
        self.sample(reader, 0, 0)
        reader.close()
        restarted = self.reader()
        self.sample(restarted, 0, 0)
        self.append(rx=50, tx=60)
        self.sample(restarted, 50, 60, seconds=10)

    def test_truncation_and_regrowth_beyond_previous_offset(self):
        self.append()
        reader = self.reader()
        (self.directory / "one.access.log").write_bytes(b"")
        self.sample(reader, 0, 0)
        self.append(rx=10, tx=20)
        self.sample(reader, 10, 20)
        (self.directory / "one.access.log").write_bytes(b"")
        for _ in range(4):
            self.append(rx=500, tx=600)
        self.sample(reader, 2000, 2400)
        self.sample(reader, 0, 0)

    def test_rotation_drains_old_inode_and_reads_replacement(self):
        self.append()
        reader = self.reader()
        self.append(rx=10, tx=20)
        replacement = self.directory / "replacement"
        replacement.mkdir()
        new_log = replacement / "one.access.log"
        new_log.write_text(json.dumps({"received_bytes": 30, "sent_bytes": 40}) + "\n")
        # Simulate the active pathname pointing at a new inode, retaining the old handle.
        # This also runs on Windows, which disallows renaming an open Python file.
        with patch.object(Path, "iterdir", return_value=iter([new_log])):
            self.sample(reader, 40, 60)
        self.append(rx=50, tx=60)  # Late writes from a worker still using the old inode.
        with patch.object(Path, "iterdir", return_value=iter([new_log])):
            self.sample(reader, 50, 60)
        with patch.object(Path, "iterdir", return_value=iter([new_log])):
            self.sample(reader, 0, 0, seconds=61)
        self.assertEqual(len(reader.files), 1)

    def test_malformed_legacy_partial_and_oversized_lines(self):
        reader = self.reader()
        log = self.directory / "one.access.log"
        log.write_bytes(b'bad json\n{}\n[]\n{"received_bytes":-1,"sent_bytes":5}\n'
                        b'{"received_bytes":true,"sent_bytes":5}\n' + b'x' * (1024 * 1024 + 3) + b'\n'
                        b'{"received_bytes":10,')
        self.sample(reader, 0, 0)
        with log.open("ab") as handle:
            handle.write(b'"sent_bytes":20}\n')
        self.sample(reader, 10, 20)
        self.append()
        self.sample(reader, 100, 200)

    def test_restart_in_middle_of_line_skips_existing_request(self):
        log = self.directory / "one.access.log"
        log.write_bytes(b'{"received_bytes":100,')
        reader = self.reader()
        with log.open("ab") as handle:
            handle.write(b'"sent_bytes":200}\n')
        self.append(rx=10, tx=20)
        self.sample(reader, 10, 20)

    def test_permission_error_is_unavailable_and_recovers(self):
        reader = self.reader()
        self.now += 5
        with patch.object(Path, "iterdir", side_effect=PermissionError()):
            self.assertEqual(reader.sample(), {"nginx_rx_bps": None, "nginx_tx_bps": None})
        self.sample(reader, 0, 0)

    def test_failed_initial_discovery_does_not_replay_backlog(self):
        self.append(rx=100000, tx=200000)
        with patch.object(Path, "iterdir", side_effect=PermissionError()):
            reader = self.reader()
        self.sample(reader, 0, 0)
        self.append(rx=10, tx=20)
        self.sample(reader, 10, 20)

    def test_logging_format_records_full_request_and_response_bytes(self):
        config = (Path(__file__).resolve().parent.parent / "deploy/00-proxy-admin-logging.conf").read_text()
        self.assertIn('"received_bytes":$request_length', config)
        self.assertIn('"sent_bytes":$bytes_sent', config)
        self.assertIn('"bytes":$body_bytes_sent', config)  # Preserve the audit viewer field.
