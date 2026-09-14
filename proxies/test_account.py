from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from .models import AccountProfile


class AccountTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("account-user", password="Old-Secure-Pass-782!")
        self.other = get_user_model().objects.create_user("other", email="other@example.com")

    def test_login_required(self):
        for name in ("account_information", "account_contact", "account_password"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)
            self.assertEqual(self.client.post(reverse(name), {}).status_code, 302)

    def test_information_displays_current_account(self):
        self.user.email = "account@example.com"
        self.user.save(update_fields=["email"])
        AccountProfile.objects.create(user=self.user, mobile_number="+91 9876543210")
        AccountProfile.objects.create(user=self.other, mobile_number="+44 1234567890")
        self.client.force_login(self.user)
        response = self.client.get(reverse("account_information"))
        self.assertContains(response, "account@example.com")
        self.assertContains(response, "+91 9876543210")
        self.assertNotContains(response, self.other.email)
        self.assertNotContains(response, "+44 1234567890")
        self.assertIn("no-store", response["Cache-Control"])

    def test_contact_updates_only_current_user(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("account_contact"), {
            "email": "new@example.com", "mobile_number": "+91 9876543210", "user": self.other.pk,
        })
        self.assertRedirects(response, reverse("account_information"))
        self.user.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.user.email, "new@example.com")
        self.assertEqual(self.other.email, "other@example.com")
        self.assertEqual(AccountProfile.objects.get(user=self.user).mobile_number, "+91 9876543210")

    def test_invalid_contact_not_saved(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("account_contact"), {"email": "invalid", "mobile_number": "abc"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AccountProfile.objects.exists())

    def test_mobile_requires_digits_with_valid_length(self):
        self.client.force_login(self.user)
        for number in ("-------", "+ ()- ()", "+1 23456", "1234567890123456", "123+456789"):
            with self.subTest(number=number):
                response = self.client.post(reverse("account_contact"), {
                    "email": "new@example.com", "mobile_number": number,
                })
                self.assertEqual(response.status_code, 200)
                self.assertIn("mobile_number", response.context["form"].errors)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "")
        self.assertFalse(AccountProfile.objects.exists())

    def test_existing_contact_can_be_changed_or_cleared(self):
        AccountProfile.objects.create(user=self.user, mobile_number="+91 9876543210")
        self.client.force_login(self.user)
        for email, number in (("updated@example.com", "+44 (123) 456-7890"), ("", "")):
            with self.subTest(number=number):
                self.assertRedirects(self.client.post(reverse("account_contact"), {
                    "email": email, "mobile_number": number,
                }), reverse("account_information"))
                self.user.refresh_from_db()
                self.assertEqual(self.user.email, email)
                self.assertEqual(AccountProfile.objects.get(user=self.user).mobile_number, number)
                self.assertEqual(AccountProfile.objects.filter(user=self.user).count(), 1)

    def test_account_changes_require_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        for name in ("account_contact", "account_password"):
            with self.subTest(name=name):
                self.assertEqual(client.post(reverse(name), {}).status_code, 403)

    def test_password_change_requires_old_password_and_keeps_session(self):
        self.client.force_login(self.user)
        data = {"old_password": "wrong", "new_password1": "New-Secure-Pass-493!", "new_password2": "New-Secure-Pass-493!"}
        self.assertEqual(self.client.post(reverse("account_password"), data).status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Old-Secure-Pass-782!"))
        data["old_password"] = "Old-Secure-Pass-782!"
        self.assertRedirects(self.client.post(reverse("account_password"), data), reverse("account_information"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(data["new_password1"]))
        self.assertEqual(self.client.get(reverse("account_information")).status_code, 200)

    def test_password_validation_and_confirmation_are_required(self):
        self.client.force_login(self.user)
        for first, second in (("short", "short"), ("New-Secure-Pass-493!", "Different-Secure-Pass-826!")):
            with self.subTest(first=first):
                response = self.client.post(reverse("account_password"), {
                    "old_password": "Old-Secure-Pass-782!", "new_password1": first, "new_password2": second,
                })
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.user.refresh_from_db()
                self.assertTrue(self.user.check_password("Old-Secure-Pass-782!"))

    def test_menu_and_logout(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("account_information"))
        self.assertContains(response, 'class="account-dropdown"')
        self.assertContains(response, "Add or change mobile number")
        self.assertEqual(self.client.post(reverse("logout")).status_code, 302)
        self.assertEqual(self.client.get(reverse("account_information")).status_code, 302)
