"""Push host metrics over verified HTTPS; no listening port or root privileges required."""
import json
import logging
import os
import time
import urllib.request
from urllib.parse import urlsplit
import psutil


def network_rates(previous, current, elapsed, stats):
    interfaces = []
    for name, counters in current.items():
        link = stats.get(name)
        if name not in previous or not link or not link.isup or name.lower() in {"lo", "lo0", "loopback pseudo-interface 1"}:
            continue
        before = previous[name]
        interfaces.append({"name": name,
                           "rx": max(0, counters.bytes_recv - before.bytes_recv) / elapsed,
                           "tx": max(0, counters.bytes_sent - before.bytes_sent) / elapsed,
                           "speed_mbps": max(0, link.speed)})
    return interfaces[:128]


def collect(previous, elapsed):
    current = psutil.net_io_counters(pernic=True)
    memory = psutil.virtual_memory()
    disks = []
    seen = set()
    for partition in psutil.disk_partitions(all=False):
        if partition.mountpoint in seen:
            continue
        seen.add(partition.mountpoint)
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except OSError:
            continue
        if usage.total:
            disks.append({"mount": partition.mountpoint, "used": usage.used, "total": usage.total, "percent": usage.percent})
    return {"cpu": psutil.cpu_percent(interval=None),
            "ram": {"used": memory.total - memory.available, "total": memory.total, "percent": memory.percent},
            "disks": disks[:128],
            "interfaces": network_rates(previous, current, elapsed, psutil.net_if_stats())}, current


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    url = os.environ["MONITOR_URL"]
    token = os.environ["MONITOR_TOKEN"]
    address = os.environ["MONITOR_ADDRESS"]
    if urlsplit(url).scheme != "https":
        raise SystemExit("MONITOR_URL must use HTTPS with a trusted certificate.")
    opener = urllib.request.build_opener(NoRedirect())
    previous = psutil.net_io_counters(pernic=True)
    psutil.cpu_percent(interval=None)  # Prime the CPU counter before the first sample.
    last = time.monotonic()
    while True:
        time.sleep(5)
        now = time.monotonic()
        try:
            metrics, previous = collect(previous, now - last)
            last = now
            request = urllib.request.Request(url, data=json.dumps(metrics).encode(), method="POST", headers={
                "Content-Type": "application/json", "Authorization": f"Bearer {token}", "X-Monitor-Address": address,
            })
            with opener.open(request, timeout=5) as response:
                response.read(1024)
        except Exception as exc:
            logging.warning("Metric delivery failed (%s); retrying", type(exc).__name__)


if __name__ == "__main__":
    main()
