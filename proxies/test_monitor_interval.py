"""Standalone agent startup tests; no database or network access required."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch


class MonitorIntervalTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "monitor_agent", Path(__file__).resolve().parent.parent / "ops/monitor-agent.py"
        )
        self.agent = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"psutil": Mock()}):
            spec.loader.exec_module(self.agent)
        self.env = {
            "MONITOR_URL": "https://monitor.example/monitor/ingest/",
            "MONITOR_TOKEN": "test-token-never-log",
            "MONITOR_ADDRESS": "192.0.2.1",
        }

    def assert_interval(self, value, expected):
        if value is not None:
            self.env["MONITOR_INTERVAL"] = value
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(self.agent, "NginxTraffic"), \
                patch.object(self.agent.urllib.request, "build_opener"), \
                patch.object(self.agent.time, "sleep", side_effect=KeyboardInterrupt) as sleep:
            with self.assertRaises(KeyboardInterrupt):
                self.agent.main()
            sleep.assert_called_once_with(expected)

    def test_default_interval(self):
        self.assert_interval(None, 3)

    def test_configured_interval(self):
        for value in (3, 10, 60, 3600):
            with self.subTest(value=value):
                self.assert_interval(str(value), value)

    def assert_invalid(self, value):
        self.env["MONITOR_INTERVAL"] = value
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(self.agent, "NginxTraffic") as traffic, \
                patch.object(self.agent.urllib.request, "build_opener") as opener, \
                patch.object(self.agent.time, "sleep") as sleep:
            with self.assertRaises(SystemExit) as raised:
                self.agent.main()
            self.assertEqual(str(raised.exception),
                             "MONITOR_INTERVAL must be an integer between 3 and 3600 seconds.")
            self.assertNotIn(self.env["MONITOR_TOKEN"], str(raised.exception))
            self.agent.psutil.net_io_counters.assert_not_called()
            traffic.assert_not_called()
            opener.assert_not_called()
            sleep.assert_not_called()

    def test_below_minimum(self):
        for value in ("2", "0", "-1"):
            with self.subTest(value=value):
                self.assert_invalid(value)

    def test_above_maximum(self):
        self.assert_invalid("3601")

    def test_non_integer(self):
        for value in ("", "abc", "30.5", "30.0", "1e2", "test-token-never-log"):
            with self.subTest(value=value):
                self.assert_invalid(value)
