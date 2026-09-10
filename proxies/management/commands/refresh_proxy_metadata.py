from django.core.management.base import BaseCommand

from proxies.forms import certificate_valid_until, resolve_public_ip
from proxies.models import CertificateBundle, ProxyConfig


class Command(BaseCommand):
    help = "Resolve proxy public IPs and extract certificate validity dates."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Refresh values that are already populated as well as missing values.",
        )

    def handle(self, *args, **options):
        refresh_all = options["all"]
        proxy_count = 0
        certificate_count = 0

        proxies = ProxyConfig.objects.all()
        if not refresh_all:
            proxies = proxies.filter(public_ip__isnull=True)
        for proxy in proxies:
            public_ip = resolve_public_ip(proxy.domain_name)
            if public_ip and (refresh_all or not proxy.public_ip):
                proxy.public_ip = public_ip
                proxy.save(update_fields=["public_ip", "updated_at"])
                proxy_count += 1
                self.stdout.write(f"{proxy.domain_name}: {public_ip}")

        certificates = CertificateBundle.objects.all()
        if not refresh_all:
            certificates = certificates.filter(valid_until__isnull=True)
        for certificate in certificates:
            try:
                valid_until = certificate_valid_until(certificate.certificate)
            except (OSError, ValueError):
                self.stderr.write(self.style.WARNING(f"{certificate.name}: certificate could not be parsed"))
                continue
            if refresh_all or not certificate.valid_until:
                certificate.valid_until = valid_until
                certificate.save(update_fields=["valid_until"])
                certificate_count += 1
                self.stdout.write(f"{certificate.name}: valid until {valid_until}")

        self.stdout.write(self.style.SUCCESS(
            f"Updated {proxy_count} public IP(s) and {certificate_count} certificate date(s)."
        ))
