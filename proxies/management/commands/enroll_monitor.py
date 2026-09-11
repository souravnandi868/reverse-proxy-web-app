import hashlib
import ipaddress
import secrets
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from proxies.models import ServerMonitor


class Command(BaseCommand):
    help = "Select the NGINX reverse proxy host and issue its monitoring token (rotates its previous token)."

    def add_arguments(self, parser):
        parser.add_argument("address")

    def handle(self, *args, **options):
        try:
            address = str(ipaddress.ip_address(options["address"]))
        except ValueError as exc:
            raise CommandError("Enter a valid server IP address.") from exc
        token = secrets.token_urlsafe(32)
        with transaction.atomic():
            ServerMonitor.objects.filter(is_reverse_proxy=True).update(is_reverse_proxy=False)
            ServerMonitor.objects.update_or_create(address=address, defaults={
                "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                "latest": {}, "received_at": None, "is_reverse_proxy": True,
            })
        self.stdout.write(f"NGINX reverse proxy server: {address}\nMONITOR_TOKEN={token}\nRun the agent on this NGINX host only.")
