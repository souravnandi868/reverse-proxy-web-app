from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import ProxyConfig


class LivePageTests(TestCase):
    def test_pages_return_fresh_saved_entries(self):
        staff = get_user_model().objects.create_user(username="live-staff", is_staff=True)
        self.client.force_login(staff)
        for name in ["dashboard", "proxy_list", "domains", "certificates"]:
            response = self.client.get(reverse(name))
            self.assertContains(response, "data-live-region=")
            self.assertContains(response, "js/live-pages.js")
            self.assertIn("no-store", response["Cache-Control"])
        proxy = ProxyConfig.objects.create(
            domain_name="live.example.org", backend_private_ip="10.0.0.9", backend_port=8080,
            created_by=staff, updated_by=staff,
        )
        for name in ["dashboard", "proxy_list", "domains"]:
            self.assertContains(self.client.get(reverse(name)), proxy.domain_name)
        proxy.delete()
        for name in ["dashboard", "proxy_list", "domains"]:
            self.assertNotContains(self.client.get(reverse(name)), "live.example.org")

    def test_edit_forms_are_not_refresh_regions(self):
        staff = get_user_model().objects.create_user(username="editor", is_staff=True)
        self.client.force_login(staff)
        self.assertNotContains(self.client.get(reverse("proxy_create")), "data-live-region=")
