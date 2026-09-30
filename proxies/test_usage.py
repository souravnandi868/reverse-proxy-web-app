from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from .models import TrafficEvent
from .usage import summary


class UsageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user("usage-staff", is_staff=True)
        self.now = timezone.now()

    def event(self, domain="one.example", ago=15, **values):
        data = {"received_bytes": 100, "sent_bytes": 200, "request_time": .2, "status": 200}
        data.update(values)
        return TrafficEvent.objects.create(domain=domain, occurred_at=self.now - timedelta(seconds=ago), data=data)

    def test_totals_filters_time_bounds_and_graph_buckets(self):
        self.event()
        self.event(status=502, request_time=.4)
        self.event("two.example", received_bytes=500)
        self.event(ago=181)
        self.event(ago=-1)
        with patch("proxies.usage.timezone.now", return_value=self.now):
            result = summary(180, "one.example")
            all_sites = summary(180, "")
        self.assertEqual(result["total"]["requests"], 2)
        self.assertEqual(result["total"]["rx"], 200)
        self.assertEqual(result["total"]["tx"], 400)
        self.assertEqual(result["total"]["error_percent"], 50)
        self.assertAlmostEqual(result["total"]["response_ms"], 300)
        self.assertEqual(len(result["series"]), 18)
        self.assertEqual(result["series"][-2]["rx_bps"], 20)
        self.assertEqual(sum(row["requests"] for row in result["series"]), 2)
        self.assertEqual(all_sites["total"]["requests"], 3)
        self.assertEqual(len(all_sites["sites"]), 2)

    def test_missing_and_invalid_metrics_are_not_fabricated(self):
        self.event(received_bytes=None, sent_bytes=True, request_time=-1, status=None)
        self.event()
        with patch("proxies.usage.timezone.now", return_value=self.now):
            data = summary(180, "")
        self.assertIsNone(data["total"]["rx"])
        self.assertIsNone(data["total"]["tx"])
        self.assertEqual(data["total"]["response_ms"], 200)
        self.assertEqual(data["total"]["requests"], 2)

    def test_staff_only_page_and_metrics(self):
        for path in ("/usage/", "/usage/metrics/"):
            self.assertEqual(self.client.get(path).status_code, 302)
        self.client.force_login(self.user)
        self.assertContains(self.client.get("/usage/"), "Website Usage")
        self.event()
        result = self.client.get("/usage/metrics/?fqdn=ONE.EXAMPLE.&seconds=bad")
        self.assertEqual(result.json()["total"]["requests"], 1)
        self.assertIn("no-store", result["Cache-Control"])
        self.user.is_staff = False
        self.user.save()
        for path in ("/usage/", "/usage/metrics/"):
            self.assertEqual(self.client.get(path).status_code, 403)

    def test_history_survives_refresh_and_does_not_open_logs(self):
        self.event()
        self.client.force_login(self.user)
        for _ in range(2):
            cache.clear()
            with patch("proxies.traffic_reader.TrafficReader.poll", side_effect=AssertionError("Unexpected log read")):
                self.assertEqual(self.client.get("/usage/metrics/").json()["total"]["requests"], 1)

    def test_timestamp_backfill_retains_old_requests_and_skips_invalid_dates(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection
        migration = import_module("proxies.migrations.0011_trafficevent_occurred_at")
        valid = TrafficEvent.objects.create(domain="old.example", data={"time": "2026-10-01T09:00:00+05:30"})
        invalid = TrafficEvent.objects.create(domain="old.example", data={"time": "invalid"})
        migration.backfill(apps, SimpleNamespace(connection=connection))
        valid.refresh_from_db()
        invalid.refresh_from_db()
        self.assertEqual(valid.occurred_at.isoformat(), "2026-10-01T03:30:00+00:00")
        self.assertIsNone(invalid.occurred_at)
