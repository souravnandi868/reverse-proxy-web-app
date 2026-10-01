import json
import socket
from dataclasses import dataclass
from pathlib import Path
from django.conf import settings
from django.utils import timezone
from .models import ConfigurationBackup

SOCKET_PATH = Path(getattr(settings, "NGINX_OPS_SOCKET", "/run/nginx-proxy-admin/ops.sock"))


@dataclass
class OperationResult:
    ok: bool
    message: str


def render_proxy_config(proxy):
    certificate = proxy.certificate_bundle
    ssl_block = ""
    if proxy.incoming_protocol == "https" and certificate:
        ssl_block = f"    ssl_certificate {certificate.certificate.path};\n    ssl_certificate_key {certificate.private_key.path};\n"
    listen = "443 ssl" if proxy.incoming_protocol == "https" else "80"
    upstream_tls = ""
    if proxy.backend_protocol == "https":
        upstream_tls = "        proxy_ssl_server_name on;\n        proxy_ssl_verify off;\n"
    connection_settings = "        proxy_read_timeout 60s;\n"
    if proxy.websocket_enabled:
        # Other request headers, including Authorization, pass through normally.
        connection_settings = (
            "        proxy_http_version 1.1;\n"
            "        proxy_set_header Upgrade $http_upgrade;\n"
            '        proxy_set_header Connection "upgrade";\n'
            "        proxy_read_timeout 3600s;\n"
            "        proxy_send_timeout 3600s;\n"
            "        proxy_buffering off;\n"
        )
    log_path = f"/var/log/nginx/proxy-admin/{proxy.domain_name}.access.log"
    error_log_path = f"/var/log/nginx/proxy-admin/{proxy.domain_name}.error.log"
    captive_locations = ""
    captive_preamble = ""
    log_format = "proxy_admin"
    if proxy.captive_portal_enabled:
        from .captive_nginx import locations, preamble
        captive_preamble, cookie_variable, log_format = preamble(proxy)
        captive_locations = f'    if ($host != {proxy.domain_name}) {{ return 444; }}\n' + locations(proxy)
        connection_settings += f"        proxy_set_header Cookie ${cookie_variable};\n"
        connection_settings += "        auth_request /_captive_auth;\n        error_page 401 = @captive_login;\n"
    return f"""# Managed by NGINX Proxy Admin. Do not edit manually.\n{captive_preamble}server {{\n    listen {listen};\n    server_name {proxy.domain_name};\n    access_log {log_path} {log_format};\n    error_log {error_log_path} warn;\n{ssl_block}{captive_locations}    location / {{\n        proxy_pass {proxy.backend_protocol}://{proxy.backend_private_ip}:{proxy.backend_port};\n{upstream_tls}        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto $scheme;\n        proxy_connect_timeout 5s;\n{connection_settings}    }}\n}}\n"""


def traffic_events(fqdn=""):
    from .models import TrafficEvent

    events = TrafficEvent.objects.all()
    fqdn = fqdn.strip().lower().rstrip(".")
    return events.filter(domain=fqdn) if fqdn else events


def iter_traffic_logs(fqdn="", start=None, end=None):
    """Export retained indexed history; never open NGINX logs in HTTP workers."""
    from django.db.models import Q
    from django.utils.dateparse import parse_datetime
    from django.utils.timezone import is_naive

    events = traffic_events(fqdn)
    if start is None and end is None:
        yield from events.values_list("data", flat=True).iterator(chunk_size=500)
        return
    bounds = Q()
    if start is not None:
        bounds &= Q(occurred_at__gte=start)
    if end is not None:
        bounds &= Q(occurred_at__lte=end)
    # A collector still running old code after migration 0011 can write rows
    # without occurred_at. Retain date filtering using the original log time.
    events = events.filter(bounds | Q(occurred_at__isnull=True))
    for occurred, data in events.values_list("occurred_at", "data").iterator(chunk_size=500):
        if occurred is None:
            try:
                occurred = parse_datetime(str(data.get("time", "")))
            except ValueError:
                continue
            if occurred is None or is_naive(occurred):
                continue
            if (start is not None and occurred < start) or (end is not None and occurred > end):
                continue
        yield data


def recent_traffic_logs(limit=100, fqdn=""):
    return list(traffic_events(fqdn).values_list("data", flat=True)[:limit])


def traffic_page(fqdn="", before=""):
    """Keyset pagination keeps old pages stable as new events arrive."""
    events = traffic_events(fqdn)
    try:
        cursor = int(before)
        if 0 < cursor <= 9223372036854775807:
            events = events.filter(id__lt=cursor)
    except (ValueError, TypeError):
        pass
    page = list(events[:101])
    return {"traffic_logs": [event.data for event in page[:100]],
            "next_before": page[99].pk if len(page) > 100 else None,
            "before": before, "selected_fqdn": fqdn}


def next_backup_version(proxy):
    latest = proxy.backups.order_by("-version").first()
    return (latest.version + 1) if latest else 1


def save_backup(proxy, actor, reason):
    return ConfigurationBackup.objects.create(proxy=proxy, version=next_backup_version(proxy), rendered_config=render_proxy_config(proxy), created_by=actor, reason=reason)


def _request(action, payload):
    if not SOCKET_PATH.exists():
        return OperationResult(False, "Privileged NGINX service is unavailable. No change was applied.")
    request = json.dumps({"action": action, "payload": payload}).encode("utf-8")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            # Validation and reload each have a 15-second helper timeout.
            client.settimeout(40)
            client.connect(str(SOCKET_PATH))
            client.sendall(request)
            response = json.loads(client.recv(4096).decode("utf-8"))
    except (OSError, ValueError) as exc:
        return OperationResult(False, f"Privileged service request failed: {exc}")
    if not isinstance(response, dict):
        return OperationResult(False, "Invalid privileged service response.")
    return OperationResult(response.get("ok") is True, response.get("message", "Operation failed."))


def apply_proxy(proxy):
    return _request("apply", {"domain": proxy.domain_name, "config": render_proxy_config(proxy), "enabled": proxy.enabled})


def rollback_proxy(proxy, backup):
    return _request("rollback", {"domain": proxy.domain_name, "config": backup.rendered_config})


def test_backend(proxy):
    try:
        with socket.create_connection((proxy.backend_private_ip, proxy.backend_port), timeout=3):
            return OperationResult(True, "Backend accepted a TCP connection.")
    except OSError as exc:
        return OperationResult(False, f"Backend connectivity failed: {exc}")
