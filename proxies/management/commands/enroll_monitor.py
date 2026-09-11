import hashlib
import ipaddress
import secrets
from django.core.management.base import BaseCommand, CommandError
from proxies.models import ServerMonitor


class Command(BaseCommand):
    help = "Enroll a server IP and issue a new monitoring token (revokes its previous token)."

    def add_arguments(self, parser):
        parser.add_argument("address")

    def handle(self, *args, **options):
        try:
            address = str(ipaddress.ip_address(options["address"]))
        except ValueError as exc:
            raise CommandError("Enter a valid server IP address.") from exc
        token = secrets.token_urlsafe(32)
        ServerMonitor.objects.update_or_create(address=address, defaults={
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "latest": {}, "received_at": None,
        })
        self.stdout.write(f"Server: {address}\nMONITOR_TOKEN={token}\nStore this token on that server only.")
