import base64

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from proxyadmin.captcha import SESSION_KEY


class LoginCaptchaTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("captcha-user", password="Secure-Pass-782!")
        self.url = reverse("login")

    def challenge(self):
        response = self.client.get(self.url)
        self.assertContains(response, "data:image/svg+xml;base64,")
        image = response.context["captcha_image"].split(",", 1)[1]
        svg = base64.b64decode(image).decode("utf-8")
        self.assertIn("<svg", svg)
        answer = self.client.session[SESSION_KEY]["answer"]
        self.assertTrue(any(char.islower() for char in answer))
        self.assertTrue(any(char.isupper() for char in answer))
        self.assertTrue(any(char.isdigit() for char in answer))
        return answer

    def credentials(self, captcha):
        return {"username": self.user.username, "password": "Secure-Pass-782!", "captcha": captcha}

    def test_valid_code_allows_login_and_cannot_be_reused(self):
        code = self.challenge()
        response = self.client.post(self.url, self.credentials(code))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("dashboard"))
        self.client.post(reverse("logout"))
        response = self.client.post(self.url, self.credentials(code))
        self.assertEqual(response.status_code, 200)
        self.assertIn("captcha", response.context["form"].errors)

    def test_missing_wrong_and_wrong_case_codes_block_login_and_rotate(self):
        for submitted in ("", "wrong!", None):
            with self.subTest(submitted=submitted):
                code = self.challenge()
                response = self.client.post(self.url, self.credentials(code.swapcase() if submitted is None else submitted))
                self.assertEqual(response.status_code, 200)
                self.assertIn("captcha", response.context["form"].errors)
                self.assertNotIn("_auth_user_id", self.client.session)
                self.assertNotEqual(self.client.session[SESSION_KEY]["answer"], code)

    def test_expired_code_is_rejected(self):
        code = self.challenge()
        session = self.client.session
        session[SESSION_KEY] = {"answer": code, "created": 0}
        session.save()
        response = self.client.post(self.url, self.credentials(code))
        self.assertIn("captcha", response.context["form"].errors)
