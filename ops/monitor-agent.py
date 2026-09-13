"""Push host metrics over verified HTTPS; no listening port or root privileges required."""
import json
import logging
import os
import stat
import time
from pathlib import Path
import urllib.request
from urllib.error import HTTPError
from urllib.parse import urlsplit
import psutil


class NginxTraffic:
    """Tail managed access logs. Restart at EOF rather than replaying old traffic."""

    def __init__(self, directory="/var/log/nginx/proxy-admin", clock=time.monotonic):
        self.directory = Path(directory)
        self.clock = clock
        self.files = {}
        self.started = False
        self.last = clock()
        self.sample()  # Establish offsets before the first measurement interval.

    def close(self):
        for state in self.files.values():
            state["file"].close()
        self.files.clear()

    def sample(self):
        now = self.clock()
        elapsed = now - self.last
        self.last = now
        rx = tx = 0
        active = set()
        available = True
        try:
            paths = list(self.directory.iterdir())
            for path in paths:
                if not path.name.endswith(".access.log") or path.is_symlink():
                    continue
                try:
                    handle = path.open("rb")
                except FileNotFoundError:
                    continue  # Rotated between directory discovery and open.
                except OSError:
                    available = False
                    continue
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):
                    handle.close()
                    continue
                key = (info.st_dev, info.st_ino)
                active.add(key)
                if key in self.files:
                    handle.close()
                    continue
                offset = info.st_size if not self.started else 0
                handle.seek(max(0, offset - 64))
                anchor = handle.read(offset - handle.tell())
                self.files[key] = {"file": handle, "offset": offset, "anchor": anchor,
                                   "skip": bool(anchor and not anchor.endswith(b"\n")), "seen": now}
        except OSError:
            available = False
        if available:
            self.started = True
        for key, state in list(self.files.items()):
            handle = state["file"]
            try:
                offset = state["offset"]
                size = os.fstat(handle.fileno()).st_size
                handle.seek(max(0, offset - len(state["anchor"])))
                # Also detect copytruncate followed by regrowth past the old offset.
                if size < offset or handle.read(len(state["anchor"])) != state["anchor"]:
                    offset = 0
                    state["skip"] = False
                handle.seek(offset)
                while handle.tell() < size:
                    start = handle.tell()
                    line = handle.readline(min(1024 * 1024, size - start))
                    if not line.endswith(b"\n"):
                        if len(line) < 1024 * 1024:
                            handle.seek(start)  # Wait for the rest of this entry.
                            break
                        state["skip"] = True  # Bound memory for malformed huge lines.
                        continue
                    if state["skip"]:
                        state["skip"] = False
                        continue
                    try:
                        entry = json.loads(line)
                        received, sent = entry["received_bytes"], entry["sent_bytes"]
                        if any(type(n) is not int or not 0 <= n <= 1e18 for n in (received, sent)):
                            continue
                        rx += received
                        tx += sent
                    except (ValueError, KeyError, TypeError, UnicodeError):
                        continue
                new_offset = handle.tell()
                if key in active or new_offset != state["offset"]:
                    state["seen"] = now
                state["offset"] = new_offset
                handle.seek(max(0, new_offset - 64))
                state["anchor"] = handle.read(new_offset - handle.tell())
                # Drain renamed files while NGINX reopens logs, then release them.
                if key not in active and now - state["seen"] >= 60:
                    handle.close()
                    del self.files[key]
            except OSError:
                available = False
                handle.close()
                del self.files[key]
        if not available or elapsed <= 0:
            return {"nginx_rx_bps": None, "nginx_tx_bps": None}
        return {"nginx_rx_bps": rx / elapsed, "nginx_tx_bps": tx / elapsed}


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
    traffic = NginxTraffic()
    psutil.cpu_percent(interval=None)  # Prime the CPU counter before the first sample.
    last = time.monotonic()
    failed = False
    while True:
        time.sleep(5)
        now = time.monotonic()
        try:
            metrics, previous = collect(previous, now - last)
            last = now
            metrics.update(traffic.sample())
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
