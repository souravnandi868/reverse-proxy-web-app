from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from .models import AuditLog, CertificateBundle, ProxyConfig


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
