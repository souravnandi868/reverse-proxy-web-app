"""Run with: python manage.py test tests.browser_navigation (requires Playwright and Chrome)."""
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from playwright.sync_api import sync_playwright, expect

from proxies.models import ProxyConfig


class NavigationBrowserTests(StaticLiveServerTestCase):
    def test_traffic_filter_and_excel_without_navigation(self):
        import json
        from io import BytesIO
        from pathlib import Path
        from openpyxl import load_workbook

        get_user_model().objects.create_superuser("traffic-admin", password="test-password")
        with TemporaryDirectory() as logs, self.settings(NGINX_ACCESS_LOG_DIR=logs), sync_playwright() as playwright:
            Path(logs, "test.access.log").write_text("\n".join(json.dumps({
                "destination_fqdn": domain, "time": "2026-09-14T12:00:00Z", "request": "GET /",
            }) for domain in ["one.example.org", "two.example.org"]), encoding="utf-8")
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page()
            page.goto(self.live_server_url + "/login/?next=/audit/")
            page.locator('[name="username"]').fill("traffic-admin")
            page.locator('[name="password"]').fill("test-password")
            page.get_by_role("button", name="Sign in", exact=True).click()
            expect(page.locator("#traffic-rows")).to_contain_text("two.example.org")
            page.evaluate("window.documentToken = 'unchanged'")
            page.locator("#traffic-fqdn").fill("one.example.org")
            page.get_by_role("button", name="Filter", exact=True).click()
            expect(page.locator("#traffic-rows")).to_contain_text("one.example.org")
            expect(page.locator("#traffic-rows")).not_to_contain_text("two.example.org")
            with page.expect_response(lambda response: "/audit/rows/?fqdn=one.example.org" in response.url):
                page.wait_for_timeout(5500)
            expect(page.locator("#traffic-rows")).not_to_contain_text("two.example.org")
            for label, count in [("Export current view to Excel", 2), ("Export all to Excel", 3)]:
                with page.expect_download() as download:
                    page.get_by_role("link", name=label, exact=True).click()
                workbook = load_workbook(BytesIO(Path(download.value.path()).read_bytes()))
                self.assertEqual(workbook.active.max_row, count)
                workbook.close()
            page.get_by_role("link", name="Show all", exact=True).click()
            expect(page.locator("#traffic-rows")).to_contain_text("two.example.org")
            self.assertEqual(page.evaluate("window.documentToken"), "unchanged")
            browser.close()

    def test_workflows_keep_the_same_document(self):
        get_user_model().objects.create_superuser("browser-admin", password="test-password")
        with TemporaryDirectory() as media, self.settings(MEDIA_ROOT=media), patch("proxies.forms.certificate_valid_until", return_value=date(2030, 1, 1)), patch("proxies.forms.resolve_public_ip", return_value="8.8.8.8"), patch(
            "proxies.views.apply_proxy", return_value=SimpleNamespace(ok=True, message="Applied")
        ), sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(self.live_server_url + "/login/")
            page.evaluate("window.documentToken = 'unchanged'")
            page.locator('[name="username"]').fill("browser-admin")
            page.locator('[name="password"]').fill("test-password")
            page.get_by_role("button", name="Sign in", exact=True).click()
            expect(page.locator(".stats")).to_be_visible()
            page.get_by_role("link", name="+ Add Reverse Proxy").click()
            page.locator('[name="domain_name"]').fill("not-a-domain")
            page.locator('[name="backend_private_ip"]').fill("10.0.0.5")
            page.locator('[name="backend_port"]').fill("8080")
            page.locator('[name="incoming_protocol"]').select_option("http")
            page.get_by_role("button", name="Save configuration").click()
            expect(page.locator(".errorlist")).to_be_visible()
            page.locator('[name="domain_name"]').fill("browser.example.org")
            page.get_by_role("button", name="Save configuration").click()
            expect(page.locator("#proxies")).to_contain_text("browser.example.org")
            page.locator('#proxies button[title="Toggle"]').click()
            expect(page.locator("#proxies")).to_contain_text("Disabled")
            page.locator('#proxies button[title="Apply"]').click()
            expect(page.locator(".messages")).to_contain_text("Applied")
            page.locator('.sidebar a[href="/proxies/"]').click()
            expect(page.locator("h1")).to_have_text("Reverse Proxies")
            with page.expect_download() as download:
                page.get_by_role("link", name="Export all as PDF").click()
            self.assertEqual(download.value.suggested_filename, "reverse-proxies.pdf")
            page.locator('.sidebar a[href="/domains/"]').click()
            expect(page.locator("h1")).to_have_text("Domains & IPs")
            page.go_back()
            expect(page.locator("h1")).to_have_text("Reverse Proxies")
            page.go_forward()
            expect(page.locator("h1")).to_have_text("Domains & IPs")
            page.locator('.sidebar a[href="/servers/"]').click()
            expect(page.locator("#monitor-status")).to_contain_text("Updated")
            page.locator('.sidebar a[href="/audit/"]').click()
            expect(page.locator("#traffic-refresh-status")).to_contain_text("Updated")
            page.locator('.sidebar a[href="/servers/"]').click()
            expect(page.locator("#monitor-status")).to_contain_text("Updated")
            page.locator('.sidebar a[href="/certificates/"]').click()
            page.locator('[name="name"]').fill("Keep this unfinished upload")
            # Background inventory updates must not reset the upload form.
            page.wait_for_timeout(5500)
            expect(page.locator('[name="name"]')).to_have_value("Keep this unfinished upload")
            page.locator('[name="certificate"]').set_input_files({
                "name": "test.pem", "mimeType": "application/x-pem-file",
                "buffer": b"-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----",
            })
            page.locator('[name="private_key"]').set_input_files({
                "name": "test.key", "mimeType": "application/x-pem-file",
                "buffer": b"-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
            })
            page.get_by_role("button", name="Upload securely").click()
            expect(page.locator(".certificate-row")).to_contain_text("Keep this unfinished upload")
            page.once("dialog", lambda dialog: dialog.dismiss())
            page.locator(".certificate-row button").click()
            expect(page.locator(".certificate-row")).to_be_visible()
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator(".certificate-row button").click()
            expect(page.locator(".certificate-row")).to_have_count(0)

            page.locator('.sidebar a[href="/proxies/"]').click()
            expect(page.locator("#proxies")).to_contain_text("browser.example.org")
            # Changes from another session appear without a document navigation.
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(lambda: ProxyConfig.objects.filter(domain_name="browser.example.org").update(backend_port=9090)).result()
            expect(page.locator("#proxies")).to_contain_text("9090", timeout=10000)
            page.locator('#proxies a[title="Delete"]').click()
            expect(page.locator("h1")).to_have_text("Delete proxy")
            page.get_by_role("button", name="Delete proxy", exact=True).click()
            expect(page.locator("#proxies")).not_to_contain_text("browser.example.org")
            page.get_by_role("button", name="Sign out").click()
            expect(page.locator(".login-panel")).to_be_visible()
            self.assertEqual(page.evaluate("window.documentToken"), "unchanged")
            self.assertEqual(errors, [])
            browser.close()
