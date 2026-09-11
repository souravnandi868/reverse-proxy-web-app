"""Authenticated metric ingestion and staff-only snapshots."""
import hashlib
import hmac
import ipaddress
import json
import math
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from .models import ServerMonitor
from .views import staff_required


def validate_metrics(data):
    def number(value, maximum=1e18):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
            raise ValueError("Invalid measurement")
        return value

    def label(value):
        if not isinstance(value, str) or not 1 <= len(value) <= 256:
            raise ValueError("Invalid label")
        return value

    def capacity(value):
        result = {key: number(value[key]) for key in ("used", "total")}
        result["percent"] = number(value["percent"], 100)
        if result["used"] > result["total"] or result["total"] == 0:
            raise ValueError("Invalid capacity")
        return result

    disks, interfaces = data["disks"], data["interfaces"]
    if not isinstance(disks, list) or not isinstance(interfaces, list) or len(disks) > 128 or len(interfaces) > 128:
        raise ValueError("Invalid device list")
    return {
        "cpu": number(data["cpu"], 100), "ram": capacity(data["ram"]),
        "disks": [dict(capacity(d), mount=label(d["mount"]),
                       free=number(d.get("free", d["total"] - d["used"]), d["total"])) for d in disks],
        "interfaces": [{"name": label(n["name"]), "rx": number(n["rx"]), "tx": number(n["tx"]),
                        "speed_mbps": number(n["speed_mbps"], 1e9)} for n in interfaces],
    }


@csrf_exempt
@require_POST
def ingest(request):
    # Agents authenticate with per-server bearer tokens, never browser cookies.
    if int(request.META.get("CONTENT_LENGTH") or 0) > 65536:
        return JsonResponse({"error": "Payload too large"}, status=413)
    try:
        address = str(ipaddress.ip_address(request.headers.get("X-Monitor-Address", "")))
    except ValueError:
        return JsonResponse({"error": "Unauthorized"}, status=401)
    authorization = request.headers.get("Authorization", "")
    monitor = ServerMonitor.objects.filter(address=address, is_reverse_proxy=True).first()
    if not monitor or not authorization.startswith("Bearer ") or not hmac.compare_digest(
        monitor.token_hash, hashlib.sha256(authorization[7:].encode()).hexdigest()
    ):
        return JsonResponse({"error": "Unauthorized"}, status=401)
    try:
        body = request.body
        if len(body) > 65536:
            return JsonResponse({"error": "Payload too large"}, status=413)
        metrics = validate_metrics(json.loads(body))
    except (ValueError, KeyError, TypeError, AttributeError):
        return JsonResponse({"error": "Invalid metrics"}, status=400)
    monitor.latest = metrics
    monitor.received_at = timezone.now()
    monitor.save(update_fields=["latest", "received_at"])
    return JsonResponse({"ok": True})


def snapshots():
    now = timezone.now()
    result = []
    for monitor in ServerMonitor.objects.filter(is_reverse_proxy=True):
        received = monitor.received_at
        status = "waiting" if received is None else (
            "live" if (now - received).total_seconds() <= 30 else "stale")
        result.append({"address": monitor.address, "name": "NGINX reverse proxy server", "status": status,
                       "received_at": received.isoformat() if received else None,
                       "metrics": monitor.latest if received else None})
    return result


@staff_required
@require_GET
@never_cache
def servers(request):
    return render(request, "proxies/servers.html")


@staff_required
@require_GET
@never_cache
def server_metrics(request):
    return JsonResponse({"servers": snapshots()})
