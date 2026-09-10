import ipaddress
import re
from django import forms
from .models import CertificateBundle, ProxyConfig

DOMAIN_RE = re.compile(r"^(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}\Z")


class ProxyConfigForm(forms.ModelForm):
    class Meta:
        model = ProxyConfig
        fields = ["domain_name", "public_ip", "backend_private_ip", "backend_port", "incoming_protocol", "backend_protocol", "certificate_bundle", "nat_notes", "firewall_notes", "enabled"]
        widgets = {
            "domain_name": forms.TextInput(attrs={"placeholder": "app.example.com"}),
            "public_ip": forms.TextInput(attrs={"placeholder": "203.0.113.10"}),
            "backend_private_ip": forms.TextInput(attrs={"placeholder": "10.20.30.40"}),
            "backend_port": forms.NumberInput(attrs={"placeholder": "8080", "min": "1", "max": "65535"}),
            "incoming_protocol": forms.Select(attrs={"placeholder": "HTTPS"}),
            "backend_protocol": forms.Select(attrs={"placeholder": "HTTP or HTTPS"}),
            "certificate_bundle": forms.Select(attrs={"placeholder": "Select an active certificate"}),
            "nat_notes": forms.Textarea(attrs={"rows": 2, "placeholder": "Example: Public NAT terminates on this NGINX host."}),
            "firewall_notes": forms.Textarea(attrs={"rows": 2, "placeholder": "Example: Permit TCP 443 from the corporate edge."}),
        }

    def clean_domain_name(self):
        domain = self.cleaned_data["domain_name"].strip().lower().rstrip(".")
        if not DOMAIN_RE.fullmatch(domain) or ".." in domain:
            raise forms.ValidationError("Use a fully qualified domain such as app.example.com.")
        return domain

    def clean_backend_private_ip(self):
        value = self.cleaned_data["backend_private_ip"]
        ipaddress.ip_address(value)
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("incoming_protocol") == "https" and not cleaned.get("certificate_bundle"):
            self.add_error("certificate_bundle", "HTTPS proxies require an active certificate bundle.")
        return cleaned


class CertificateBundleForm(forms.ModelForm):
    class Meta:
        model = CertificateBundle
        fields = ["name", "certificate", "private_key", "valid_until"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "wildcard-example-com"}),
            "certificate": forms.ClearableFileInput(attrs={"accept": ".pem,.crt,.cer"}),
            "private_key": forms.ClearableFileInput(attrs={"accept": ".pem,.key"}),
            "valid_until": forms.DateInput(attrs={"type": "date"}),
        }

    def _validate_pem(self, uploaded, marker):
        if uploaded.size > 1024 * 1024:
            raise forms.ValidationError("Certificate files must be 1 MB or smaller.")
        uploaded.seek(0)
        content = uploaded.read()
        uploaded.seek(0)
        if marker not in content:
            raise forms.ValidationError("The uploaded file is not a recognized PEM file.")

    def clean_certificate(self):
        value = self.cleaned_data["certificate"]
        self._validate_pem(value, b"-----BEGIN CERTIFICATE-----")
        return value

    def clean_private_key(self):
        value = self.cleaned_data["private_key"]
        self._validate_pem(value, b"-----BEGIN")
        return value
