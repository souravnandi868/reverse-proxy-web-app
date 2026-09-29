"""Bounded incremental log ingestion. Only the background worker opens logs."""
import json
import logging
import math
import os
from pathlib import Path
import stat
import time
from collections import deque
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import TrafficCursor, TrafficEvent


class TrafficReader:
    # Limits bound startup work, each read, memory, and retained history.
    READ_BYTES = 256 * 1024
    CYCLE_BYTES = 4 * 1024 * 1024
    MAX_LINE = 16 * 1024
    HISTORY_EVENTS = 100_000
    BUFFER_EVENTS = 1000
    FIELDS = {"time", "source_ip", "destination_fqdn", "destination_server",
              "request", "status", "request_time", "bytes", "user_agent"}

    def __init__(self, directory):
        self.directory = Path(directory)
        self.events = deque(maxlen=self.BUFFER_EVENTS)
        self.bytes_read = 0
        self.last_prune = 0
        self.next_file = 0

    def _read(self, handle, size):
        data = handle.read(size)
        self.bytes_read += len(data)
        return data

    def _consume(self, handle, cursor, size, budget, name):
        offset = cursor.offset
        anchor = bytes(cursor.anchor)
        if offset:
            handle.seek(max(0, offset - len(anchor)))
            if size < offset or self._read(handle, len(anchor)) != anchor:
                offset = 0  # copytruncate, including regrowth beyond old EOF
                cursor.skipping = False
        handle.seek(offset)
        data = self._read(handle, min(self.READ_BYTES, budget, max(0, size - offset)))
        end = data.rfind(b"\n") + 1
        if end:
            complete = data[:end].splitlines()
            offset += end
        else:
            complete = []
        # Do not keep unbounded partial lines in memory or reread them forever.
        if not end and len(data) >= self.MAX_LINE:
            offset += len(data)
            cursor.skipping = True
        records = []
        for line in complete:
            if cursor.skipping:
                cursor.skipping = False
                continue
            if len(line) > self.MAX_LINE:
                continue
            try:
                entry = json.loads(line)
            except (ValueError, UnicodeError, RecursionError):
                continue
            if not isinstance(entry, dict):
                continue
            # Never store arbitrary fields such as authorization headers or tokens.
            entry = {key: value for key, value in entry.items()
                     if key in self.FIELDS and isinstance(value, (str, int, float, type(None)))
                     and not (isinstance(value, float) and not math.isfinite(value))}
            domain = str(entry.get("destination_fqdn", "")).lower().rstrip(".")[:253]
            entry["log_file"] = name
            records.append(TrafficEvent(domain=domain, data=entry))
        cursor.offset = offset
        handle.seek(max(0, offset - 64))
        cursor.anchor = self._read(handle, min(offset, 64))
        return records

    def poll(self):
        self.bytes_read = 0
        count = 0
        # Include renamed logs to drain writes from old NGINX file descriptors.
        paths = sorted(self.directory.glob("*.access.log*"))
        if paths:
            start = self.next_file % len(paths)
            paths = paths[start:] + paths[:start]
        seen = set()
        for path in paths:
            if self.bytes_read >= self.CYCLE_BYTES:
                break
            self.next_file += 1
            if path.is_symlink() or path.suffix in {".gz", ".bz2", ".xz", ".zip"}:
                continue
            try:
                # O_NOFOLLOW closes the symlink race on production Linux.
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
                with os.fdopen(fd, "rb") as handle:
                    info = os.fstat(handle.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    identity = f"{info.st_dev}:{info.st_ino}"
                    if identity in seen:
                        continue
                    seen.add(identity)
                    with transaction.atomic():
                        cursor, created = TrafficCursor.objects.select_for_update().get_or_create(identity=identity)
                        previous = (cursor.offset, bytes(cursor.anchor), cursor.skipping)
                        if created:
                            # Bootstrap only a bounded tail, never scan the existing file.
                            cursor.offset = max(0, info.st_size - self.READ_BYTES)
                            cursor.skipping = cursor.offset > 0
                        records = self._consume(handle, cursor, info.st_size,
                                                self.CYCLE_BYTES - self.bytes_read, path.name)
                        TrafficEvent.objects.bulk_create(records)
                        if (created or previous != (cursor.offset, cursor.anchor, cursor.skipping)
                                or cursor.updated_at < timezone.now() - timedelta(hours=1)):
                            cursor.save()
                    self.events.extend(record.data for record in records)
                    count += len(records)
            except FileNotFoundError:
                continue  # Rotation raced discovery; try again next cycle.
            except OSError as exc:
                # Log neither source lines nor configuration/credential values.
                logging.warning("Traffic log unavailable (%s); retrying next cycle.", type(exc).__name__)
        newest = TrafficEvent.objects.order_by("-id").values_list("id", flat=True).first()
        if count and newest and newest > self.HISTORY_EVENTS:
            TrafficEvent.objects.filter(id__lte=newest - self.HISTORY_EVENTS).delete()
        if time.monotonic() - self.last_prune >= 60:
            TrafficCursor.objects.filter(updated_at__lt=timezone.now() - timedelta(days=7)).delete()
            self.last_prune = time.monotonic()
        return count
