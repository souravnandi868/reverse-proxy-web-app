import ipaddress
import re
import socket
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from django import forms
from .models import CertificateBundle, ProxyConfig

DOMAIN_RE = re.compile(r"^(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}\Z")


def resolve_public_ip(domain):
    """Return the first globally routable address published for a domain."""
    try:
        addresses = socket.getaddrinfo(domain, None, type=socket.SOCK_STREAM)
    except OSError:
        return None

    for address in addresses:
        candidate = address[4][0]
        try:
            if ipaddress.ip_address(candidate).is_global:
                return candidate
        except ValueError:
            continue
    return None


def certificate_valid_until(uploaded):
    uploaded.seek(0)
    certificate = x509.load_pem_x509_certificate(uploaded.read(), default_backend())
    uploaded.seek(0)
    if hasattr(certificate, "not_valid_after_utc"):
        return certificate.not_valid_after_utc.date()
    return certificate.not_valid_after.date()


class ProxyConfigForm(forms.ModelForm):
    class Meta:
        model = ProxyConfig
        fields = ["domain_name", "backend_private_ip", "backend_port", "incoming_protocol", "backend_protocol", "certificate_bundle", "nat_notes", "firewall_notes", "enabled"]
        help_texts = {
            "domain_name": "The public IP is resolved automatically from this FQDN on save. Scheduled DNS refresh checks it every 5 minutes when enabled.",
        }
        widgets = {
            "domain_name": forms.TextInput(attrs={"placeholder": "app.example.com"}),
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
        if cleaned.get("domain_name"):
            public_ip = resolve_public_ip(cleaned["domain_name"])
            if public_ip:
                cleaned["public_ip"] = public_ip
                self._resolved_public_ip = public_ip
            else:
                self.add_error("domain_name", "The FQDN does not resolve to a public IP address.")
        if cleaned.get("incoming_protocol") == "https" and not cleaned.get("certificate_bundle"):
            self.add_error("certificate_bundle", "HTTPS proxies require an active certificate bundle.")
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.public_ip = self._resolved_public_ip
        if commit:
            instance.save()
        return instance


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["valid_until"].required = False
        self.fields["valid_until"].help_text = "Read automatically from the uploaded certificate."

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
        try:
            self._certificate_valid_until = certificate_valid_until(value)
        except ValueError as exc:
            raise forms.ValidationError("The certificate could not be parsed as a valid PEM certificate.") from exc
        return value

    def clean(self):
        cleaned = super().clean()
        if hasattr(self, "_certificate_valid_until"):
            cleaned["valid_until"] = self._certificate_valid_until
        return cleaned

    def clean_private_key(self):
        value = self.cleaned_data["private_key"]
        self._validate_pem(value, b"-----BEGIN")
        return value
