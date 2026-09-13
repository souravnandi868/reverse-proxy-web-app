"""Push host metrics over verified HTTPS; no listening port or root privileges required."""
import json
import logging
import os
import stat
import time
import urllib.request
from urllib.error import HTTPError
from urllib.parse import urlsplit
import psutil


def network_rates(previous, current, elapsed, stats):
    interfaces = []
    for name, counters in current.items():
        link = stats.get(name)
        if name.lower() in {"lo", "lo0", "loopback pseudo-interface 1"}:
            continue
        before = previous.get(name)
        available = before is not None and elapsed > 0
        interfaces.append({"name": name,
                           "rx": max(0, counters.bytes_recv - before.bytes_recv) / elapsed if available else 0,
                           "tx": max(0, counters.bytes_sent - before.bytes_sent) / elapsed if available else 0,
                           "speed_mbps": max(0, link.speed) if link else 0,
                           "is_up": link.isup if link else None,
                           "rate_available": available})
    return interfaces[:128]


def nginx_storage(path="/var/log/nginx/"):
    # Sum regular file lengths recursively; never follow links outside the tree.
    total = 0
    seen = set()
    pending = [path]
    try:
        if not stat.S_ISDIR(os.stat(path.rstrip("/"), follow_symlinks=False).st_mode):
            return []
        while pending:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if os.name == "nt":
                            # Windows DirEntry stat may omit file identity.
                            info = os.stat(entry.path, follow_symlinks=False)
                    except FileNotFoundError:
                        continue  # A log may disappear during rotation.
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(entry.path)
                    elif stat.S_ISREG(info.st_mode):
                        identity = (info.st_dev, info.st_ino)
                        if identity not in seen:
                            seen.add(identity)
                            total += info.st_size
    except OSError:
        # Do not publish a misleading partial total when traversal fails.
        return []
    return [{"mount": path, "kind": "directory", "used": total}]


def collect(previous, elapsed):
    current = psutil.net_io_counters(pernic=True)
    memory = psutil.virtual_memory()
    disks = nginx_storage()
    return {"cpu": psutil.cpu_percent(interval=None),
            "ram": {"used": memory.total - memory.available, "total": memory.total, "percent": memory.percent},
            "disks": disks[:128],
            "interfaces": network_rates(previous, current, elapsed, psutil.net_if_stats())}, current


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def report_http_error(error):
    hints = {
        400: "Check Django ALLOWED_HOSTS and the agent metric format.",
        401: "Check MONITOR_ADDRESS and MONITOR_TOKEN against the current NGINX host enrollment.",
        403: "Check NGINX access rules and any authentication gateway in front of Django.",
        404: "Set MONITOR_URL to the admin app's /monitor/ingest/ endpoint and deploy the updated Django routes.",
        405: "Check that /monitor/ingest/ is routed to Django and accepts POST.",
        413: "Check the request body size limit in NGINX.",
        500: "Check the Django error log and run pending database migrations.",
        502: "Check that the Django service is running and NGINX can reach it.",
        503: "Check the Django service and any maintenance or access gateway.",
        504: "Check the Django service for timeouts.",
    }
    hint = "Check the NGINX and Django error logs."
    if 300 <= error.code < 400:
        hint = "Redirect refused. Use the final HTTPS admin URL with /monitor/ingest/ and its trailing slash; check TLS redirect settings."
    else:
        hint = hints.get(error.code, hint)
    # Never log URLs, headers, or response bodies: they can contain credentials.
    logging.warning("Metric delivery failed: HTTP %s. %s Retrying.", error.code, hint)


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
    failed = False
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
            if failed:
                logging.warning("Metric delivery recovered; measurements are being accepted.")
                failed = False
        except HTTPError as exc:
            failed = True
            report_http_error(exc)
            exc.close()
        except Exception as exc:
            failed = True
            logging.warning("Metric delivery failed (%s); retrying", type(exc).__name__)


if __name__ == "__main__":
    main()
