from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from proxies.captive_models import CaptiveRateLimit


class Command(BaseCommand):
    help = "Delete expired authentication rate buckets; run daily to bound storage."

    def handle(self, *args, **options):
        retention = max(86400, settings.AUTH_RATE_WINDOW_SECONDS, settings.CAPTIVE_RESEND_SECONDS, 3600)
        count, _ = CaptiveRateLimit.objects.filter(
            window_start__lt=timezone.now() - timedelta(seconds=retention)).delete()
        self.stdout.write(f"Removed {count} expired rate buckets.")
