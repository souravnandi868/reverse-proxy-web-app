from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from datetime import date, timedelta
from unittest.mock import patch
from .models import AuditLog, CertificateBundle, ProxyConfig


class CertificateExpiryTests(TestCase):
    def test_expiry_boundaries_and_page_labels(self):
        user = get_user_model().objects.create_user("expiry-staff", is_staff=True)
        self.client.force_login(user)
        today = date(2026, 10, 1)
        cases = [(31, "healthy", "31 days remaining"), (30, "warning", "30 days remaining"),
                 (15, "warning", "15 days remaining"), (14, "critical", "14 days remaining"),
                 (1, "critical", "1 day remaining"), (0, "critical", "Expires today"),
                 (-1, "critical", "Expired 1 day ago"), (None, "unknown", "Expiry date not recorded")]
        with patch("proxies.models.timezone.localdate", return_value=today):
            for days, state, label in cases:
                cert = CertificateBundle.objects.create(name=f"Certificate {days}",
                    certificate="certificates/test.pem", private_key="keys/test.pem", uploaded_by=user,
                    valid_until=today + timedelta(days=days) if days is not None else None)
                self.assertEqual(cert.expiry, {"state": state, "label": label})
                ProxyConfig.objects.create(domain_name=f"site-{cert.pk}.example.org", backend_private_ip="10.0.0.5",
                    backend_port=80, certificate_bundle=cert, created_by=user, updated_by=user)
            for path in ("/", "/certificates/", "/proxies/"):
                response = self.client.get(path)
                for _, state, label in cases:
                    self.assertContains(response, f"expiry-{state}")
                    self.assertContains(response, label)
            response = self.client.get("/certificates/")
            self.assertContains(response, "Available Certificates")
            self.assertNotContains(response, "Available bundles")


class CertificateDeletionTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("cert-admin", password="test-password")
        self.bundle = CertificateBundle.objects.create(name='Example "TLS"', certificate="certificates/example.pem",
            private_key="keys/example.pem", uploaded_by=self.admin)
        self.url = f"/certificates/{self.bundle.pk}/delete/"
        self.client.force_login(self.admin)

    def test_delete_unused_bundle_and_audit(self):
        response = self.client.post(self.url)
        self.assertRedirects(response, "/certificates/")
        self.assertFalse(CertificateBundle.objects.filter(pk=self.bundle.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="delete", detail__type="certificate_bundle").exists())

    def test_in_use_bundle_is_preserved(self):
        ProxyConfig.objects.create(domain_name="app.example.com", backend_private_ip="10.0.0.4", backend_port=80,
            certificate_bundle=self.bundle, created_by=self.admin, updated_by=self.admin)
        response = self.client.post(self.url, follow=True)
        self.assertContains(response, "it is assigned to a proxy")
        self.assertTrue(CertificateBundle.objects.filter(pk=self.bundle.pk).exists())
        self.assertFalse(AuditLog.objects.filter(action="delete").exists())

    def test_delete_requires_post_admin_and_csrf(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(self.url).status_code, 403)
        staff = get_user_model().objects.create_user("cert-staff", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertNotContains(self.client.get("/certificates/"), "data-delete-certificate")
        self.assertTrue(CertificateBundle.objects.filter(pk=self.bundle.pk).exists())

    def test_confirmation_name_is_html_escaped(self):
        response = self.client.get("/certificates/")
        self.assertContains(response, 'data-delete-certificate="Example &quot;TLS&quot;"')
