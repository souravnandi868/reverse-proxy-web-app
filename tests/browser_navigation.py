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
from proxies.traffic_reader import TrafficReader


class NavigationBrowserTests(StaticLiveServerTestCase):
    def test_operations_and_redirects_keep_position(self):
        from django.conf import settings
        user = get_user_model().objects.create_superuser("action-admin", password="test-password")
        proxies = ProxyConfig.objects.bulk_create([ProxyConfig(domain_name=f"action-{i:02d}.example.org",
            backend_private_ip="10.0.0.5", backend_port=8080, incoming_protocol="http",
            created_by=user, updated_by=user) for i in range(30)])
        self.client.force_login(user)
        with patch("proxies.views.apply_proxy", return_value=SimpleNamespace(ok=True, message="Applied")), patch(
            "proxies.forms.resolve_public_ip", return_value="8.8.8.8"
        ), sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1100, "height": 600})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                "value": self.client.cookies[settings.SESSION_COOKIE_NAME].value, "url": self.live_server_url}])
            page.goto(self.live_server_url + "/proxies/")
            page.evaluate("window.documentToken = 'same'; window.scrollTo(0, 700)")
            pk = proxies[0].pk
            # Toggle redirects from the list to dashboard, and apply stays there.
            for action in ("toggle", "apply"):
                with page.expect_response(lambda r: r.request.method == "POST"):
                    page.eval_on_selector(f'form[action="/proxies/{pk}/{action}/"]', "form => form.requestSubmit()")
                expect(page.locator('.messages')).to_be_visible()
                page.wait_for_timeout(200)
                self.assertAlmostEqual(page.evaluate("scrollY"), 700, delta=2)
                self.assertEqual(page.evaluate("window.documentToken"), "same")
            # Edit validation stays in place; save returns to the list position.
            page.eval_on_selector(f'a[href="/proxies/{pk}/edit/"]', "link => link.click()")
            expect(page.locator('.form-shell')).to_be_visible()
            page.evaluate("window.scrollTo(0, 200)")
            editor_position = page.evaluate("scrollY")
            page.eval_on_selector('[name="domain_name"]', "el => { el.value = 'invalid'; }")
            page.eval_on_selector('.form-shell', "form => form.requestSubmit()")
            expect(page.locator('.errorlist').first).to_be_visible()
            self.assertAlmostEqual(page.evaluate("scrollY"), editor_position, delta=2)
            page.eval_on_selector('[name="domain_name"]', "el => { el.value = 'action-00.example.org'; }")
            page.eval_on_selector('.form-shell', "form => form.requestSubmit()")
            expect(page.locator('.messages')).to_contain_text("Proxy updated")
            self.assertAlmostEqual(page.evaluate("scrollY"), 700, delta=2)
            page.eval_on_selector(f'a[href="/proxies/{pk}/delete/"]', "link => link.click()")
            expect(page.locator('h1')).to_have_text("Delete proxy")
            page.get_by_role('button', name='Delete proxy', exact=True).evaluate("button => button.form.requestSubmit()")
            expect(page.locator('.messages')).to_contain_text("Proxy and its backups deleted")
            self.assertAlmostEqual(page.evaluate("scrollY"), 700, delta=2)
            self.assertEqual(page.evaluate("window.documentToken"), "same")
            # Settings uses native Django admin forms rather than app navigation.
            page.goto(self.live_server_url + f"/admin/auth/user/{user.pk}/change/")
            page.evaluate("window.scrollTo(0, 700)")
            admin_position = page.evaluate("scrollY")
            self.assertGreater(admin_position, 100)
            page.eval_on_selector('[name="last_name"]', "el => { el.value = 'Updated'; }")
            page.eval_on_selector('input[name="_continue"]', "button => button.form.requestSubmit(button)")
            expect(page.locator('.messagelist .success')).to_be_visible()
            self.assertAlmostEqual(page.evaluate("scrollY"), admin_position, delta=2)
            self.assertEqual(errors, [])
            browser.close()

    def test_live_refresh_and_range_changes_preserve_scroll(self):
        from django.conf import settings
        from django.utils import timezone
        from datetime import timedelta
        from proxies.models import ServerMonitor, TrafficEvent

        user = get_user_model().objects.create_superuser("scroll-admin", password="test-password")
        self.client.force_login(user)
        now = timezone.now()
        ServerMonitor.objects.create(address="10.0.0.4", is_reverse_proxy=True, token_hash="test-only",
            received_at=now, latest={"cpu": 25, "ram": {"used": 400, "total": 1000, "percent": 40},
                                    "disks": [], "interfaces": []},
            history=[{"time": (now - timedelta(seconds=i * 3)).isoformat(), "cpu": 25, "ram": 40}
                     for i in reversed(range(50))])
        ProxyConfig.objects.bulk_create([ProxyConfig(domain_name=f"site-{i}.example.org",
            backend_private_ip="10.0.0.5", backend_port=8080, created_by=user, updated_by=user)
            for i in range(25)])
        TrafficEvent.objects.bulk_create([TrafficEvent(domain="site-0.example.org", occurred_at=now,
            data={"time": now.isoformat(), "destination_fqdn": "site-0.example.org", "status": 200,
                  "received_bytes": 100, "sent_bytes": 200, "request_time": .2}) for _ in range(40)])
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1100, "height": 650})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                "value": self.client.cookies[settings.SESSION_COOKIE_NAME].value, "url": self.live_server_url}])
            for route, ready, delay in [("/servers/", ".server-card", 3400),
                                        ("/usage/", ".usage-chart", 3400),
                                        ("/audit/", "#traffic-rows tr", 3400),
                                        ("/proxies/", "#proxies tbody tr", 5400),
                                        ("/", ".stats", 5400)]:
                with self.subTest(route=route):
                    page.goto(self.live_server_url + route)
                    expect(page.locator(ready).first).to_be_visible()
                    page.evaluate("window.documentToken = 'same'; window.scrollTo(0, 450)")
                    position = page.evaluate("scrollY")
                    self.assertGreater(position, 100)
                    page.wait_for_timeout(delay)
                    self.assertAlmostEqual(page.evaluate("scrollY"), position, delta=2)
                    self.assertEqual(page.evaluate("window.documentToken"), "same")
                    if route in ("/servers/", "/usage/"):
                        selector = "#resource-range" if route == "/servers/" else "#usage-range"
                        # Dispatch without Playwright scrolling the filter into view.
                        page.eval_on_selector(selector, "el => { el.value = '300'; el.dispatchEvent(new Event('change')); }")
                        page.wait_for_timeout(500)
                        self.assertAlmostEqual(page.evaluate("scrollY"), position, delta=2)
                        self.assertEqual(page.locator(selector).input_value(), "300")
            # Refreshing the current view through its own link also keeps position.
            page.eval_on_selector('.sidebar nav a[href="/"]', "el => el.click()")
            page.wait_for_timeout(500)
            self.assertAlmostEqual(page.evaluate("scrollY"), position, delta=2)
            self.assertEqual(page.evaluate("window.documentToken"), "same")
            # Mobile tables keep their horizontal position through region updates.
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(self.live_server_url + "/proxies/")
            page.locator('#proxies .table-wrap').evaluate("el => { el.scrollLeft = 150; }")
            page.wait_for_timeout(5400)
            self.assertEqual(page.locator('#proxies .table-wrap').evaluate("el => el.scrollLeft"), 150)
            self.assertEqual(errors, [])
            browser.close()

    def test_traffic_filter_and_excel_without_navigation(self):
        import json
        from io import BytesIO
        from pathlib import Path
        from openpyxl import load_workbook
        from django.conf import settings

        user = get_user_model().objects.create_superuser("traffic-admin", password="test-password")
        self.client.force_login(user)
        session_cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        with TemporaryDirectory() as logs, self.settings(NGINX_ACCESS_LOG_DIR=logs), sync_playwright() as playwright:
            Path(logs, "test.access.log").write_text("\n".join(json.dumps({
                "destination_fqdn": domain, "time": "2026-09-14T12:00:00Z", "request": "GET /",
            }) for domain in ["one.example.org", "two.example.org"]), encoding="utf-8")
            with Path(logs, "test.access.log").open("a", encoding="utf-8") as handle:
                handle.write("\n")
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(TrafficReader(logs).poll).result()
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page()
            page.context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                                      "value": session_cookie, "url": self.live_server_url}])
            page.goto(self.live_server_url + "/audit/")
            expect(page.locator("#traffic-rows")).to_contain_text("two.example.org")
            page.evaluate("window.documentToken = 'unchanged'")
            page.locator("#traffic-fqdn").fill("one.example.org")
            page.get_by_role("button", name="Filter", exact=True).click()
            expect(page.locator("#traffic-rows")).to_contain_text("one.example.org")
            expect(page.locator("#traffic-rows")).not_to_contain_text("two.example.org")
            with page.expect_response(lambda response: "/audit/rows/?fqdn=one.example.org" in response.url):
                page.wait_for_timeout(5500)
            expect(page.locator("#traffic-rows")).not_to_contain_text("two.example.org")
            for count in (2, 3):
                if count == 3:
                    page.get_by_role("link", name="Show all", exact=True).click()
                    expect(page.locator("#traffic-rows")).to_contain_text("two.example.org")
                page.locator('[name="start"]').fill("2026-09-14T17:30")
                page.locator('[name="end"]').fill("2026-09-14T17:30")
                with page.expect_download() as download:
                    page.get_by_role("button", name="Export range to Excel", exact=True).click()
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
            page.locator("#proxies").get_by_role("switch").click()
            expect(page.locator("#proxies")).to_contain_text("Disabled")
            page.locator('#proxies button[title="Apply"]').click()
            expect(page.locator(".messages")).to_contain_text("Applied")
            page.locator('.sidebar a[href="/proxies/"]').click()
            expect(page.locator("h1")).to_have_text("Reverse Proxies")
            with page.expect_download() as download:
                page.get_by_role("link", name="Export all as PDF").click()
            self.assertEqual(download.value.suggested_filename, "reverse-proxies.pdf")
            page.locator('.sidebar a[href="/usage/"]').click()
            expect(page.locator("h1")).to_have_text("Website Usage")
            page.go_back()
            expect(page.locator("h1")).to_have_text("Reverse Proxies")
            page.go_forward()
            expect(page.locator("h1")).to_have_text("Website Usage")
            page.locator('.sidebar a[href="/servers/"]').click()
            expect(page.locator("#monitor-status")).to_contain_text("Updated")
            page.locator('.sidebar a[href="/audit/"]').click()
            expect(page.locator("#traffic-refresh-status")).to_be_empty()
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
            page.locator(".account-dropdown summary").click()
            page.get_by_role("button", name="Logout", exact=True).click()
            expect(page.locator(".login-panel")).to_be_visible()
            self.assertEqual(page.evaluate("window.documentToken"), "unchanged")
            self.assertEqual(errors, [])
            browser.close()

    def test_search_and_switch_on_loaded_navigated_and_live_pages(self):
        user = get_user_model().objects.create_superuser("search-admin", password="test-password")
        domains = ["alpha.find.example.org", "zulu.find.example.org"] + [
            f"middle-{index:02d}.example.org" for index in range(14)
        ]
        ProxyConfig.objects.bulk_create([
            ProxyConfig(domain_name=domain, backend_private_ip="10.0.0.5", backend_port=8080,
                        created_by=user, updated_by=user)
            for domain in domains
        ])
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(self.live_server_url + "/login/?next=/proxies/")
            page.locator('[name="username"]').fill("search-admin")
            page.locator('[name="password"]').fill("test-password")
            page.get_by_role("button", name="Sign in", exact=True).click()
            expect(page.locator("#proxies")).to_contain_text("alpha.find.example.org")
            # Exercise initialization on a full authenticated load before testing app navigation.
            page.reload()
            page.evaluate("window.documentToken = 'unchanged'")
            first = page.locator("#proxies tbody tr").filter(has_text="alpha.find.example.org")
            last = page.locator("#proxies tbody tr").filter(has_text="zulu.find.example.org")
            toggle = first.get_by_role("switch")
            expect(toggle).to_have_attribute("aria-checked", "true")
            expect(toggle).to_have_css("background-color", "rgb(22, 163, 74)")
            expect(toggle.locator(".proxy-switch-thumb")).to_have_css("transform", "matrix(1, 0, 0, 1, 20, 0)")
            toggle.click()
            expect(toggle).to_have_attribute("aria-checked", "false")
            expect(toggle).to_have_css("background-color", "rgb(220, 38, 38)")
            expect(toggle.locator(".proxy-switch-thumb")).to_have_css("transform", "none")
            expect(first).to_contain_text("Disabled")
            toggle.click()
            expect(toggle).to_have_attribute("aria-checked", "true")
            expect(first).to_contain_text("Online")

            search = page.get_by_role("searchbox", name="Search this page", exact=True)
            status = page.locator("#search-status")
            search.fill("find.example.org")
            expect(page.locator("#proxies .search-match")).to_have_count(2)
            expect(page.locator("#proxies .search-blink")).to_have_count(2)
            expect(status).to_contain_text("2 matching items")
            # Matching rows are far apart, so Enter must visibly move between them.
            search.press("Enter")
            expect(first).to_be_in_viewport()
            search.press("Enter")
            expect(last).to_be_in_viewport()
            expect(first).not_to_be_in_viewport()
            search.press("Escape")
            expect(search).to_have_value("")
            expect(status).to_be_hidden()
            expect(page.locator(".search-match, .search-blink")).to_have_count(0)

            page.locator('.sidebar a[href="/usage/"]').click()
            expect(page.locator("h1")).to_have_text("Website Usage")
            search.fill("alpha.find.example.org")
            expect(page.locator(".search-match")).to_have_count(1)
            expect(status).to_contain_text("1 matching item")
            page.locator('.sidebar a[href="/proxies/"]').click()
            expect(page.locator("h1")).to_have_text("Reverse Proxies")
            search.fill("fresh.example.org")
            expect(status).to_contain_text("No matching items")
            # An edit from another session replaces table rows while the query remains active.
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(lambda: ProxyConfig.objects.filter(domain_name="zulu.find.example.org").update(
                    domain_name="fresh.example.org"
                )).result()
            expect(page.locator("#proxies .search-match")).to_have_count(1, timeout=10000)
            expect(page.locator("#proxies .search-match")).to_contain_text("fresh.example.org")
            expect(search).to_have_value("fresh.example.org")
            expect(status).to_contain_text("1 matching item")
            expect(page.locator("#proxies .search-blink")).to_have_count(0)
            search.fill("")
            expect(page.locator(".search-match")).to_have_count(0)
            self.assertEqual(page.evaluate("window.documentToken"), "unchanged")
            self.assertEqual(errors, [])
            browser.close()

    def test_account_menu_updates_contacts_and_password_without_navigation(self):
        import base64
        import re

        def fill_captcha(page):
            image = page.locator('.captcha-image-row img').get_attribute('src')
            svg = base64.b64decode(image.split(',', 1)[1]).decode()
            page.locator('[name="captcha"]').fill(''.join(re.findall(r'<text[^>]*>(.*?)</text>', svg)))

        get_user_model().objects.create_superuser("root", password="original-test-password")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(self.live_server_url + "/login/")
            page.evaluate("window.documentToken = 'unchanged'")
            page.locator('[name="username"]').fill("root")
            page.locator('[name="password"]').fill("original-test-password")
            fill_captcha(page)
            page.get_by_role("button", name="Sign in", exact=True).click()
            expect(page.locator(".stats")).to_be_visible()

            # Controllers and the shared theme must survive navigation from login.
            page.locator('.sidebar a[href="/usage/"]').click()
            expect(page.locator('#usage-charts .usage-chart')).to_have_count(4)
            self.assertTrue(page.evaluate("document.head.querySelector('link[rel=stylesheet]:last-of-type').hasAttribute('data-app-theme')"))
            page.locator('.sidebar nav a[href="/"]').click()
            expect(page.locator(".stats")).to_be_visible()

            menu = page.locator(".account-dropdown")
            expect(menu.locator("summary")).to_have_text("R")
            menu.locator("summary").click()
            for label in ["Account information", "Change password", "Add or change mobile number", "Add or change email ID"]:
                expect(menu.get_by_role("link", name=label, exact=True)).to_be_visible()
            expect(menu.get_by_role("button", name="Logout", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            expect(menu).not_to_have_attribute("open", "")
            menu.locator("summary").click()
            page.locator("h1").click()
            expect(menu).not_to_have_attribute("open", "")
            menu.locator("summary").click()
            menu.get_by_role("link", name="Account information", exact=True).click()
            expect(page.locator("h1")).to_have_text("Account information")
            expect(page.locator(".account-info")).to_contain_text("root")

            menu.locator("summary").click()
            menu.get_by_role("link", name="Add or change mobile number", exact=True).click()
            page.locator('[name="mobile_number"]').fill("+91 9876543210")
            page.locator('[name="email"]').fill("root@example.org")
            page.get_by_role("button", name="Save changes", exact=True).click()
            expect(page.locator(".account-info")).to_contain_text("+91 9876543210")
            expect(page.locator(".account-info")).to_contain_text("root@example.org")

            menu.locator("summary").click()
            menu.get_by_role("link", name="Add or change email ID", exact=True).click()
            expect(page.locator('[name="mobile_number"]')).to_have_value("+91 9876543210")
            expect(page.locator('[name="email"]')).to_have_value("root@example.org")
            page.locator('[name="email"]').fill("updated@example.org")
            page.get_by_role("button", name="Save changes", exact=True).click()
            expect(page.locator(".account-info")).to_contain_text("updated@example.org")

            menu.locator("summary").click()
            menu.get_by_role("link", name="Change password", exact=True).click()
            page.locator('[name="old_password"]').fill("incorrect-current-password")
            page.locator('[name="new_password1"]').fill("changed-test-password-2026!")
            page.locator('[name="new_password2"]').fill("changed-test-password-2026!")
            page.get_by_role("button", name="Save changes", exact=True).click()
            expect(page.locator(".errorlist")).to_contain_text("old password was entered incorrectly")
            page.locator('[name="old_password"]').fill("original-test-password")
            page.locator('[name="new_password1"]').fill("changed-test-password-2026!")
            page.locator('[name="new_password2"]').fill("changed-test-password-2026!")
            page.get_by_role("button", name="Save changes", exact=True).click()
            expect(page.locator(".messages")).to_contain_text("Password changed successfully.")
            expect(page.locator(".account-info")).to_contain_text("updated@example.org")
            menu.locator("summary").click()
            menu.get_by_role("button", name="Logout", exact=True).click()
            expect(page.locator(".login-panel")).to_be_visible()

            page.locator('[name="username"]').fill("root")
            page.locator('[name="password"]').fill("changed-test-password-2026!")
            fill_captcha(page)
            page.get_by_role("button", name="Sign in", exact=True).click()
            expect(page.locator(".stats")).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            expect(menu.locator("summary")).to_be_visible()
            menu.locator("summary").click()
            expect(menu.get_by_role("link", name="Account information", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            expect(menu).not_to_have_attribute("open", "")
            self.assertEqual(page.evaluate("window.documentToken"), "unchanged")
            self.assertEqual(errors, [])
            browser.close()
