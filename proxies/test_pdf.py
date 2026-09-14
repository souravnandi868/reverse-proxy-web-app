from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import ProxyConfig


class ProxyPdfTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username="pdf-staff", is_staff=True)
        self.proxy = ProxyConfig.objects.create(
            domain_name="example.org", backend_private_ip="10.0.0.1", backend_port=8080,
            created_by=self.staff, updated_by=self.staff,
            nat_notes="Forward <443> & preserve headers. " * 12,
        )
        self.url = reverse("proxy_export_pdf")

    def test_download_and_links(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn('attachment; filename="reverse-proxies.pdf"', response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertTrue(response.content.rstrip().endswith(b"%%EOF"))
        self.assertIn("no-store", response["Cache-Control"])
        for page in [reverse("proxy_list"), reverse("dashboard")]:
            self.assertContains(self.client.get(page), self.url)

    def test_access(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        user = get_user_model().objects.create_user(username="pdf-user")
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(self.url).status_code, 405)


    def test_all_entries_and_empty_export(self):
        from unittest.mock import patch
        from .pdf import build_proxy_pdf

        ProxyConfig.objects.create(
            domain_name="second.example.org", backend_private_ip="::1", backend_port=443,
            enabled=False, created_by=self.staff, updated_by=self.staff,
        )
        self.client.force_login(self.staff)
        with patch("proxies.pdf.build_proxy_pdf", wraps=build_proxy_pdf) as builder:
            self.assertEqual(self.client.get(self.url).status_code, 200)
            self.assertEqual([p.domain_name for p in builder.call_args.args[0]],
                             ["example.org", "second.example.org"])
        ProxyConfig.objects.all().delete()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_large_export(self):
        from .pdf import build_proxy_pdf
        report = build_proxy_pdf([self.proxy] * 150)
        self.assertTrue(report.startswith(b"%PDF-"))
        self.assertTrue(report.rstrip().endswith(b"%%EOF"))
