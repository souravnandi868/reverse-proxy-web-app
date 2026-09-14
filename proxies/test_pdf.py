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
        self.url = reverse("proxy_export_pdf", args=[self.proxy.pk])

    def test_download_and_links(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn('attachment; filename="reverse-proxy-exampleorg.pdf"', response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertTrue(response.content.rstrip().endswith(b"%%EOF"))
        self.assertIn("no-store", response["Cache-Control"])
        for page in [reverse("proxy_list"), reverse("proxy_edit", args=[self.proxy.pk])]:
            self.assertContains(self.client.get(page), self.url)

    def test_access_and_missing_entry(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        user = get_user_model().objects.create_user(username="pdf-user")
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(self.url).status_code, 405)
        self.assertEqual(self.client.get(reverse("proxy_export_pdf", args=[99999])).status_code, 404)
