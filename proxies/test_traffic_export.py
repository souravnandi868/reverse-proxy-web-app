import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import TestCase
from openpyxl import load_workbook

from .services import recent_traffic_logs


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

    def workbook_rows(self, query=""):
        response = self.client.get("/audit/export.xlsx" + query)
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
        self.assertEqual(len(self.workbook_rows("?fqdn=missing.example.org")), 1)

    def test_export_permissions_and_method(self):
        self.assertEqual(self.client.post("/audit/export.xlsx").status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get("/audit/export.xlsx").status_code, 302)
        self.user.is_staff = False
        self.user.save()
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/audit/export.xlsx").status_code, 403)
