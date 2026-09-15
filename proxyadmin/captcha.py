import base64
import hmac
import secrets
import string
import time

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache


SESSION_KEY = "login_captcha"
CAPTCHA_LIFETIME = 300


def new_captcha(request):
    characters = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
    ] + [secrets.choice(string.ascii_letters + string.digits) for _ in range(3)]
    secrets.SystemRandom().shuffle(characters)
    answer = "".join(characters)
    request.session[SESSION_KEY] = {"answer": answer, "created": time.time()}

    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="210" height="70" viewBox="0 0 210 70">',
             '<rect width="210" height="70" rx="8" fill="#eef5fc"/>']
    for _ in range(14):
        x1, y1, x2, y2 = (secrets.randbelow(210), secrets.randbelow(70),
                          secrets.randbelow(210), secrets.randbelow(70))
        parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#b9c9d9" stroke-width="1"/>')
    for index, character in enumerate(characters):
        x = 20 + index * 31
        y = 46 + secrets.randbelow(11) - 5
        rotation = secrets.randbelow(25) - 12
        parts.append(f'<text x="{x}" y="{y}" transform="rotate({rotation} {x} {y})" '
                     f'font-family="sans-serif" font-size="31" font-weight="bold" fill="#163d67">{character}</text>')
    parts.append('</svg>')
    encoded = base64.b64encode("".join(parts).encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


class CaptchaAuthenticationForm(AuthenticationForm):
    captcha = forms.CharField(
        label="Image code", required=False, max_length=6,
        widget=forms.TextInput(attrs={"autocomplete": "off", "autocapitalize": "none", "spellcheck": "false"}),
    )

    def clean_captcha(self):
        challenge = self.request.session.pop(SESSION_KEY, None)
        value = self.cleaned_data.get("captcha", "").strip()
        if (not challenge or time.time() - challenge["created"] > CAPTCHA_LIFETIME
                or not hmac.compare_digest(value, challenge["answer"])):
            raise forms.ValidationError("Enter the image code exactly as shown. A new code is ready below.")
        return value


@method_decorator(never_cache, name="dispatch")
class CaptchaLoginView(LoginView):
    template_name = "registration/login.html"
    authentication_form = CaptchaAuthenticationForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["captcha_image"] = new_captcha(self.request)
        return context
