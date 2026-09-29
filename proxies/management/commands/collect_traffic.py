"""Continuously index appended traffic outside HTTP request workers."""
import time
import os
from contextlib import contextmanager

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from proxies.traffic_reader import TrafficReader


@contextmanager
def collector_lock(path):
    """An OS lock is released on process exit, including crashes."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise CommandError("A traffic collector is already running for this installation.") from None
        yield
    finally:
        os.close(fd)


class Command(BaseCommand):
    help = "Index appended NGINX traffic every 2 seconds. Run exactly one worker per log directory."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        lock_path = getattr(settings, "TRAFFIC_COLLECTOR_LOCK", settings.BASE_DIR / ".traffic-collector.lock")
        with collector_lock(lock_path):
            self.collect(options)

    def collect(self, options):
        reader = TrafficReader(settings.NGINX_ACCESS_LOG_DIR)
        while True:
            started = time.monotonic()
            close_old_connections()
            reader.poll()
            if options["once"]:
                return
            time.sleep(max(0, 2 - (time.monotonic() - started)))
