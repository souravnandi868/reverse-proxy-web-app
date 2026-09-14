from django import forms
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from .models import AccountProfile


class ContactForm(forms.Form):
    email = forms.EmailField(
        label="Email ID", required=False, max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    mobile_number = forms.RegexField(
        regex=r"^\+?[0-9 ()-]{7,25}$", required=False, max_length=25,
        label="Mobile number", help_text="Include your country code, for example +91 9876543210.",
        error_messages={"invalid": "Enter a valid mobile number using digits, spaces, +, parentheses or hyphens."},
        widget=forms.TextInput(attrs={"type": "tel", "autocomplete": "tel"}),
    )

    def clean_mobile_number(self):
        value = self.cleaned_data["mobile_number"]
        if value and not 7 <= sum(char.isdigit() for char in value) <= 15:
            raise forms.ValidationError("Enter a mobile number containing 7 to 15 digits.")
        return value


@never_cache
@login_required
@require_http_methods(["GET"])
def information(request):
    profile = AccountProfile.objects.filter(user=request.user).first()
    return render(request, "registration/account_information.html", {"profile": profile})


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def contact(request):
    profile = AccountProfile.objects.filter(user=request.user).first()
    form = ContactForm(request.POST if request.method == "POST" else None, initial={
        "email": request.user.email,
        "mobile_number": profile.mobile_number if profile else "",
    })
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            request.user.email = form.cleaned_data["email"]
            request.user.save(update_fields=["email"])
            AccountProfile.objects.update_or_create(user=request.user, defaults={
                "mobile_number": form.cleaned_data["mobile_number"],
            })
        messages.success(request, "Contact information updated.")
        return redirect("account_information")
    return render(request, "registration/account_form.html", {"form": form, "page_title": "Contact information"})


@sensitive_post_parameters("old_password", "new_password1", "new_password2")
@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def password(request):
    form = PasswordChangeForm(request.user, request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Password changed successfully.")
        return redirect("account_information")
    return render(request, "registration/account_form.html", {"form": form, "page_title": "Change password"})
