import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import TestCase
from openpyxl import load_workbook

from .services import recent_traffic_logs
from .traffic_reader import TrafficReader
from .models import TrafficEvent


class TrafficExportTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = self.settings(NGINX_ACCESS_LOG_DIR=self.directory.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.user = get_user_model().objects.create_user("traffic", is_staff=True)
        self.client.force_login(self.user)
        entries = [dict(time=f"2026-09-14T12:{i // 60:02d}:{i % 60:02d}Z",
                        destination_fqdn="busy.example.org", request="GET / HTTP/1.1") for i in range(120)]
        entries.insert(0, dict(time="2026-09-13T12:00:00Z", destination_fqdn="quiet.example.org",
                               request="=1+1", user_agent="<script>test</script>\x01"))
        Path(self.directory.name, "site.access.log").write_text(
            "\n".join(json.dumps(entry) for entry in entries) + "\nnull\n[]\nbroken", encoding="utf-8")
        TrafficReader(self.directory.name).poll()

    def workbook_rows(self, query=""):
        dates = urlencode({"start": "2026-09-13T00:00:00", "end": "2026-09-15T00:00:00"})
        response = self.client.get("/audit/export.xlsx?" + dates + "&" + query.lstrip("?"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])
        workbook = load_workbook(BytesIO(response.content))
        self.addCleanup(workbook.close)
        return list(workbook.active.rows)

    def test_default_all_and_filter_before_limit(self):
        self.assertEqual(len(recent_traffic_logs()), 100)
        self.assertEqual(len(recent_traffic_logs(fqdn="QUIET.EXAMPLE.ORG.")), 1)
        response = self.client.get("/audit/", {"fqdn": "quiet.example.org"})
        self.assertContains(response, "=1+1")
        self.assertNotContains(response, "busy.example.org")
        response = self.client.get("/audit/rows/", {"fqdn": "QUIET.EXAMPLE.ORG."})
        self.assertIn("quiet.example.org", response.json()["html"])
        self.assertNotIn("busy.example.org", response.json()["html"])
        self.assertNotIn("<script>", response.json()["html"])

    def test_excel_all_filtered_empty_and_untrusted_text(self):
        self.assertEqual(len(self.workbook_rows()), 122)
        filtered = self.workbook_rows("?fqdn=quiet.example.org")
        self.assertEqual(len(filtered), 2)
        self.assertEqual(filtered[1][2].value, "quiet.example.org")
        self.assertEqual(filtered[1][4].value, "=1+1")
        self.assertEqual(filtered[1][4].data_type, "s")
        self.assertNotIn("\x01", filtered[1][8].value)
        response = self.client.get("/audit/export.xlsx", {
            "start": "2026-09-13T00:00", "end": "2026-09-15T00:00", "fqdn": "missing.example.org",
        })
        self.assertContains(response, "No retained traffic matches", status_code=400)
        self.assertNotIn("Content-Disposition", response)

    def test_export_uses_log_time_when_timestamp_index_is_missing(self):
        TrafficEvent.objects.update(occurred_at=None)
        # This simulates an older collector still running after the schema update.
        self.assertEqual(len(self.workbook_rows()), 122)
        response = self.client.get("/audit/export.xlsx", {
            "start": "2026-09-14T17:30:30", "end": "2026-09-14T17:30:32",
            "fqdn": "busy.example.org",
        })
        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(BytesIO(response.content))
        self.addCleanup(workbook.close)
        self.assertEqual(workbook.active.max_row, 4)

    def test_unknown_timestamps_do_not_bypass_date_filter(self):
        TrafficEvent.objects.all().delete()
        for value in ("invalid", "2026-09-14T12:00:00", "2026-99-99T00:00:00Z"):
            TrafficEvent.objects.create(domain="busy.example.org", data={"time": value})
        response = self.client.get("/audit/export.xlsx", {
            "start": "2026-09-13T00:00", "end": "2026-09-15T00:00",
        })
        self.assertContains(response, "No retained traffic matches", status_code=400)

    def test_export_permissions_and_method(self):
        self.assertEqual(self.client.post("/audit/export.xlsx").status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get("/audit/export.xlsx").status_code, 302)
        self.user.is_staff = False
        self.user.save()
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/audit/export.xlsx").status_code, 403)

    def test_inclusive_range_converts_ist_and_filters_before_export(self):
        response = self.client.get("/audit/export.xlsx", {
            "start": "2026-09-14T17:30:30", "end": "2026-09-14T17:30:32",
            "fqdn": "BUSY.EXAMPLE.ORG.",
        })
        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(BytesIO(response.content))
        self.addCleanup(workbook.close)
        rows = list(workbook.active.values)
        self.assertEqual(len(rows), 4)
        self.assertEqual({row[0] for row in rows[1:]}, {
            "2026-09-14T12:00:30Z", "2026-09-14T12:00:31Z", "2026-09-14T12:00:32Z",
        })

    def test_missing_invalid_or_reversed_ranges_do_not_download(self):
        for dates in ({}, {"start": "bad", "end": "2026-09-15T00:00"},
                      {"start": "2026-09-15T00:00", "end": "2026-09-14T00:00"},
                      {"start": "2026-09-15T00:00"}):
            with self.subTest(dates=dates):
                response = self.client.get("/audit/export.xlsx", dates)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.context["export_form"].errors)
                self.assertNotIn("Content-Disposition", response)
