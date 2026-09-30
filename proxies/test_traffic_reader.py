import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase

from .models import TrafficCursor, TrafficEvent
from .services import traffic_page
from .traffic_reader import TrafficReader
from .management.commands.collect_traffic import collector_lock


class TrafficReaderTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "site.access.log"
        self.reader = TrafficReader(self.root)

    def append(self, request="GET /", path=None):
        with (path or self.path).open("ab") as handle:
            handle.write(json.dumps({"request": request, "destination_fqdn": "site.example",
                                     "MONITOR_TOKEN": "must-not-be-stored"}).encode() + b"\n")

    def test_restart_and_no_replay(self):
        self.append()
        self.assertEqual(self.reader.poll(), 1)
        offset = TrafficCursor.objects.get().offset
        self.assertEqual(TrafficReader(self.root).poll(), 0)
        self.append("GET /new")
        self.assertEqual(TrafficReader(self.root).poll(), 1)
        self.assertGreater(TrafficCursor.objects.get().offset, offset)
        self.assertEqual(TrafficEvent.objects.count(), 2)
        self.assertNotIn("must-not-be-stored", str(list(TrafficEvent.objects.values())))

    def test_usage_fields_and_timestamp_are_preserved(self):
        self.path.write_text(json.dumps({"time": "2026-10-01T09:00:00+05:30",
            "destination_fqdn": "SITE.EXAMPLE", "received_bytes": 123,
            "sent_bytes": 456, "request_time": .2, "authorization": "secret"}) + "\n")
        self.reader.poll()
        event = TrafficEvent.objects.get()
        self.assertEqual(event.occurred_at.isoformat(), "2026-10-01T03:30:00+00:00")
        self.assertEqual(event.data["received_bytes"], 123)
        self.assertEqual(event.data["sent_bytes"], 456)
        self.assertNotIn("authorization", event.data)

    def test_command_enforces_one_worker_and_releases_lock(self):
        lock = self.root / "collector.lock"
        with collector_lock(lock):
            with self.assertRaises(CommandError):
                with collector_lock(lock):
                    self.fail("Second collector acquired the lock")
        self.append()
        with self.settings(NGINX_ACCESS_LOG_DIR=str(self.root), TRAFFIC_COLLECTOR_LOCK=lock):
            call_command("collect_traffic", once=True)
        self.assertEqual(TrafficEvent.objects.count(), 1)

    def test_idle_poll_does_not_write_cursor_or_events(self):
        self.append()
        self.reader.poll()
        with self.captureOnCommitCallbacks(), patch.object(TrafficCursor, "save") as save:
            self.assertEqual(self.reader.poll(), 0)
            save.assert_not_called()

    def test_rotation_drains_old_file_and_reads_new_file(self):
        self.append("old")
        self.reader.poll()
        rotated = self.path.with_suffix(".log.1")
        self.path.rename(rotated)
        self.append("late old", rotated)
        self.append("new")
        self.assertEqual(self.reader.poll(), 2)
        self.assertEqual(self.reader.poll(), 0)
        self.assertEqual(TrafficCursor.objects.count(), 2)
        self.assertEqual({event.data["request"] for event in TrafficEvent.objects.all()},
                         {"old", "late old", "new"})

    def test_copytruncate_and_regrowth(self):
        self.append("old")
        self.reader.poll()
        self.path.write_bytes(b"")
        self.append("replacement longer than the original line" * 3)
        self.assertEqual(self.reader.poll(), 1)
        self.path.write_bytes(b"")
        self.append("short")
        self.assertEqual(self.reader.poll(), 1)

    def test_partial_and_oversized_lines(self):
        self.path.write_bytes(b'{"request":"partial"')
        self.assertEqual(self.reader.poll(), 0)
        self.assertEqual(TrafficCursor.objects.get().offset, 0)
        with self.path.open("ab") as handle:
            handle.write(b'}\n' + b'x' * (self.reader.MAX_LINE + 1))
        self.assertEqual(self.reader.poll(), 1)
        self.assertEqual(self.reader.poll(), 0)
        with self.path.open("ab") as handle:
            handle.write(b'ignored continuation\n')
        self.append("valid")
        self.assertEqual(self.reader.poll(), 1)

    def test_malformed_records_do_not_stop_collection(self):
        self.path.write_bytes(b'not json\n[]\nnull\n\xff\n' + b'[' * 2000 + b']' * 2000 + b'\n'
                              + b'{"request":"valid", "bytes":NaN}\n')
        self.assertEqual(self.reader.poll(), 1)
        self.assertNotIn("bytes", TrafficEvent.objects.get().data)

    def test_cycle_read_budget_and_fairness(self):
        self.reader.CYCLE_BYTES = 1024
        self.reader.READ_BYTES = 1024
        for i in range(3):
            path = self.root / f"{i}.access.log"
            path.write_bytes(b"\n" * 4096)
        for _ in range(3):
            self.reader.poll()
            self.assertLessEqual(self.reader.bytes_read, self.reader.CYCLE_BYTES + 128)
        self.assertEqual(TrafficCursor.objects.count(), 3)

    def test_event_and_offset_commit_together(self):
        self.append()
        with patch.object(TrafficEvent.objects, "bulk_create", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.reader.poll()
        self.assertEqual(TrafficCursor.objects.count(), 0)
        self.assertEqual(self.reader.poll(), 1)

    def test_bounded_buffer_and_history(self):
        self.reader.HISTORY_EVENTS = 10
        for i in range(1100):
            self.append(str(i))
        self.reader.poll()
        self.assertEqual(len(self.reader.events), self.reader.BUFFER_EVENTS)
        self.assertEqual(TrafficEvent.objects.count(), 10)

    def test_history_keyset_pagination_stable_during_ingestion(self):
        for i in range(120):
            self.append(str(i))
        self.reader.poll()
        first = traffic_page("site.example")
        self.assertEqual(len(first["traffic_logs"]), 100)
        self.append("new")
        self.reader.poll()
        older = traffic_page("site.example", str(first["next_before"]))
        self.assertEqual(len(older["traffic_logs"]), 20)
        self.assertFalse({entry["request"] for entry in first["traffic_logs"]}
                         & {entry["request"] for entry in older["traffic_logs"]})

    def test_http_pages_and_export_never_discover_or_read_logs(self):
        self.append()
        self.reader.poll()
        self.client.force_login(get_user_model().objects.create_user("reader", is_staff=True))
        with patch.object(Path, "glob", side_effect=AssertionError("HTTP scanned logs")), \
                patch.object(TrafficReader, "poll", side_effect=AssertionError("HTTP ingested logs")):
            for url in ("/", "/audit/", "/audit/rows/", "/audit/export.xlsx"):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b"must-not-be-stored", response.content)

    def test_500_mb_log_work_depends_only_on_appended_lines(self):
        results = []
        for megabytes in (1, 500):
            folder = self.root / str(megabytes)
            folder.mkdir()
            path = folder / "performance.access.log"
            # Sparse file simulates size without allocating/writing 500 MB.
            with path.open("wb") as handle:
                handle.seek(megabytes * 1024 * 1024 - 1)
                handle.write(b"\n")
            reader = TrafficReader(folder)
            reader.poll()
            self.assertLessEqual(reader.bytes_read, reader.READ_BYTES + 128)
            self.assertEqual(TrafficCursor.objects.get(
                identity=f"{path.stat().st_dev}:{path.stat().st_ino}").offset, path.stat().st_size)
            measurements = []
            for iteration in range(3):
                for i in range(50):
                    self.append(f"GET /{iteration}/{i}", path)
                start = perf_counter()
                with patch("proxies.traffic_reader.json.loads", wraps=json.loads) as parse:
                    self.assertEqual(reader.poll(), 50)
                    self.assertEqual(parse.call_count, 50)
                measurements.append((perf_counter() - start, reader.bytes_read))
            self.assertEqual(reader.poll(), 0)
            self.assertLessEqual(reader.bytes_read, 128)
            results.append(measurements)
        self.assertEqual([size for _, size in results[0]], [size for _, size in results[1]])
        small = min(duration for duration, _ in results[0])
        large = min(duration for duration, _ in results[1])
        # Byte and parser counts above are deterministic; generous timing bound
        # catches size-dependent scans without relying on noisy microbenchmarks.
        self.assertLess(large, max(0.5, small * 10))
        print(f"\nTraffic append benchmark (50 lines): 1 MB={small:.4f}s, "
              f"500 MB={large:.4f}s; bytes read={results[1][0][1]} for both sizes.")
