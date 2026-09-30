"""Staff-only per-website usage from retained, timestamp-indexed requests."""
import math
from datetime import timedelta

from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .models import ProxyConfig, TrafficEvent
from .views import staff_required


def number(value):
    if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1e18:
        return value
    return None


def empty():
    return dict(requests=0, errors=0, rx=0, tx=0, rx_samples=0, tx_samples=0,
                duration=0, duration_samples=0, status_samples=0)


def add(target, data):
    target["requests"] += 1
    status = number(data.get("status"))
    if status is not None and 100 <= status <= 599:
        target["status_samples"] += 1
        target["errors"] += int(status >= 400)
    for key, source in (("rx", "received_bytes"), ("tx", "sent_bytes"), ("duration", "request_time")):
        value = number(data.get(source))
        if value is not None:
            target[key] += value
            target[key + "_samples"] += 1


def finish(row):
    count = row["requests"]
    # Partial byte coverage must not look like a complete traffic total.
    for key in ("rx", "tx"):
        if not count or row[key + "_samples"] != count:
            row[key] = None
    row["response_ms"] = row["duration"] * 1000 / row["duration_samples"] if row["duration_samples"] else None
    row["error_percent"] = row["errors"] * 100 / row["status_samples"] if row["status_samples"] else None
    return row


def summary(seconds, fqdn):
    now = timezone.now()
    start = now - timedelta(seconds=seconds)
    step = 10
    buckets = [empty() for _ in range(seconds // step)]
    domains = {}
    events = TrafficEvent.objects.filter(occurred_at__gte=start, occurred_at__lt=now)
    if fqdn:
        events = events.filter(domain=fqdn)
    total = empty()
    for domain, occurred, data in events.order_by().values_list("domain", "occurred_at", "data").iterator(chunk_size=1000):
        if not domain:
            continue
        row = domains.setdefault(domain, empty())
        add(row, data)
        add(total, data)
        index = min(len(buckets) - 1, int((occurred - start).total_seconds() // step))
        add(buckets[index], data)
    configured = dict(ProxyConfig.objects.values_list("domain_name", "backend_private_ip"))
    ports = dict(ProxyConfig.objects.values_list("domain_name", "backend_port"))
    rows = []
    for domain, row in domains.items():
        row.update(fqdn=domain, backend=f"{configured[domain]}:{ports[domain]}" if domain in configured else "Not configured")
        rows.append(finish(row))
    rows.sort(key=lambda row: (-row["requests"], row["fqdn"]))
    for index, row in enumerate(buckets):
        finish(row)
        row["time"] = (start + timedelta(seconds=(index + 1) * step)).isoformat()
        row["rx_bps"] = row["rx"] / step if row["rx"] is not None else None
        row["tx_bps"] = row["tx"] / step if row["tx"] is not None else None
    return {"start": start.isoformat(), "end": now.isoformat(), "bucket_seconds": step,
            "total": finish(total), "sites": rows, "series": buckets}


@staff_required
@require_GET
@never_cache
def page(request):
    return render(request, "proxies/usage.html", {
        "domains": ProxyConfig.objects.order_by("domain_name").values_list("domain_name", flat=True),
    })


@staff_required
@require_GET
@never_cache
def metrics(request):
    try:
        seconds = int(request.GET.get("seconds", "180"))
    except ValueError:
        seconds = 180
    if seconds not in (180, 300, 1080):
        seconds = 180
    fqdn = request.GET.get("fqdn", "").strip().lower().rstrip(".")[:253]
    # Short-lived server cache shares work between viewers; history stays in the DB.
    import hashlib
    key = f"website-usage-v1:{seconds}:{hashlib.sha256(fqdn.encode()).hexdigest()}"
    data = cache.get(key)
    if data is None:
        data = summary(seconds, fqdn)
        cache.set(key, data, 3)
    return JsonResponse(data)
