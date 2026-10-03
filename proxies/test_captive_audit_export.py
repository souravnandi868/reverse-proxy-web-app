from datetime import datetime, timedelta, timezone as dt_timezone
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from .captive_models import CaptiveAudit


@override_settings(TIME_ZONE="Asia/Kolkata")
class CaptiveAuditExportTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user("audit-viewer", is_staff=True)
        self.staff.user_permissions.add(Permission.objects.get(codename="view_captiveaudit"))
        self.url = reverse("captive_audit_export_excel")
        self.client.force_login(self.staff)

    def contents(self, response):
        self.assertEqual(response.status_code, 200)
        book = load_workbook(BytesIO(response.content))
        return book.active, list(book.active.values)

    def test_date_range_inclusive_local_time_all_pages_and_filters(self):
        start = datetime(2026, 10, 3, 4, 30, tzinfo=dt_timezone.utc)
        for index in range(55):
            event = CaptiveAudit.objects.create(actor=self.staff, action="login_success", channel="sms")
            CaptiveAudit.objects.filter(pk=event.pk).update(created_at=start + timedelta(seconds=index))
        for when, action in [(start - timedelta(seconds=1), "login_success"),
                             (start + timedelta(seconds=55), "login_success"), (start, "other")]:
            event = CaptiveAudit.objects.create(actor=self.staff, action=action)
            CaptiveAudit.objects.filter(pk=event.pk).update(created_at=when)
        params = {"start": "2026-10-03T10:00:00", "end": "2026-10-03T10:00:54",
                  "action": "login_success", "channel": "sms", "administrator": "audit", "page": 2}
        sheet, rows = self.contents(self.client.get(self.url, params))
        self.assertEqual(len(rows), 56)
        self.assertEqual(rows[0][0], "Time (Asia/Kolkata)")
        self.assertIn("10:00:54+05:30", rows[1][0])
        self.assertIn("10:00:00+05:30", rows[-1][0])
        self.assertEqual(sheet.freeze_panes, "A2")
        self.assertEqual(sheet.auto_filter.ref, "A1:G56")
        page = self.client.get(reverse("captive_audit"), params)
        self.assertEqual(page.context["page"].paginator.count, 55)
        self.assertContains(page, f'formaction="{self.url}"')

    def test_invalid_dates_and_reversed_range_rejected(self):
        for params in [{"start": "bad"}, {"end": "2026-99-03T12:00"},
                       {"start": "2026-10-04T12:00", "end": "2026-10-03T12:00"}]:
            self.assertEqual(self.client.get(self.url, params).status_code, 400)
            self.assertEqual(self.client.get(reverse("captive_audit"), params).status_code, 400)

    def test_empty_export_and_literal_formula_text(self):
        _, rows = self.contents(self.client.get(self.url))
        self.assertEqual(len(rows), 1)
        CaptiveAudit.objects.create(action='=HYPERLINK("https://example.org")')
        sheet, rows = self.contents(self.client.get(self.url))
        self.assertEqual(sheet["E2"].data_type, "s")
        self.assertEqual(rows[1][4], '=HYPERLINK("https://example.org")')

    def test_permissions_get_only_and_download_headers(self):
        response = self.client.get(self.url)
        self.assertEqual(response["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertIn('attachment; filename="captive-audit.xlsx"', response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.client.post(self.url).status_code, 405)
        self.staff.user_permissions.clear()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
