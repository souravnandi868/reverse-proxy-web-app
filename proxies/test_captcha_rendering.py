import re
from xml.etree import ElementTree

from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, SimpleTestCase
from django.template.loader import render_to_string

from proxyadmin.captcha import SESSION_KEY, new_captcha, validate_captcha


class CaptiveCaptchaRenderingTests(SimpleTestCase):
    def request(self):
        request = RequestFactory().get('/_captive/login/')
        SessionMiddleware(lambda request: None).process_request(request)
        return request

    def test_inline_image_is_visible_markup_and_matches_validated_challenge(self):
        request = self.request()
        svg = new_captcha(request, inline=True)
        root = ElementTree.fromstring(svg)
        answer = ''.join(root.itertext())
        self.assertEqual(answer, request.session[SESSION_KEY]['answer'])
        self.assertEqual(len(answer), 6)
        html = render_to_string('captive/login.html', {'captcha_svg': svg, 'proxy': {'domain_name': 'app.example.com'}})
        self.assertIn('<svg ', html)
        self.assertNotIn('&lt;svg', html)
        self.assertNotIn('data:image/svg+xml', html)
        self.assertIn('class="captcha-image" role="img"', html)
        self.assertIn('name="captcha" required', html)
        self.assertTrue(validate_captcha(request, answer))
        self.assertFalse(validate_captcha(request, answer))

    def test_existing_admin_data_image_format_is_preserved(self):
        self.assertTrue(new_captcha(self.request()).startswith('data:image/svg+xml;base64,'))

    def test_inline_image_contains_only_generated_alphanumeric_characters(self):
        root = ElementTree.fromstring(new_captcha(self.request(), inline=True))
        characters = ''.join(root.itertext())
        self.assertTrue(re.fullmatch('[a-zA-Z0-9]{6}', characters))

    def test_older_worker_image_context_still_displays_captcha(self):
        request = self.request()
        image = new_captcha(request)
        html = render_to_string('captive/login.html', {'captcha_image': image})
        self.assertIn('<img src="' + image + '"', html)
        self.assertNotIn('CAPTCHA could not load', html)
        self.assertTrue(validate_captcha(request, request.session[SESSION_KEY]['answer']))

    def test_missing_context_shows_recovery_message_instead_of_blank_image(self):
        html = render_to_string('captive/login.html', {})
        self.assertIn('CAPTCHA could not load', html)
        self.assertIn('role="alert"', html)
        self.assertNotIn('<img src=""', html)

    def test_actual_portal_view_generates_captcha(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from proxies.captive import page
        request = self.request()
        request.captive_proxy = SimpleNamespace(domain_name='app.example.com', pk=1)
        with patch('proxies.captive.current_session', return_value=None):
            response = page(request)
        self.assertIn(b'<svg ', response.content)
        self.assertNotIn(b'CAPTCHA could not load', response.content)
        from proxies.captive import CAPTIVE_CAPTCHA_LAST_KEY
        self.assertIn(CAPTIVE_CAPTCHA_LAST_KEY, request.session)
