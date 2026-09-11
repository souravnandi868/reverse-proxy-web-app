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
    log_path = f"/var/log/nginx/proxy-admin/{proxy.domain_name}.access.log"
    error_log_path = f"/var/log/nginx/proxy-admin/{proxy.domain_name}.error.log"
    return f"""# Managed by NGINX Proxy Admin. Do not edit manually.\nserver {{\n    listen {listen};\n    server_name {proxy.domain_name};\n    access_log {log_path} proxy_admin;\n    error_log {error_log_path} warn;\n{ssl_block}    location / {{\n        proxy_pass {proxy.backend_protocol}://{proxy.backend_private_ip}:{proxy.backend_port};\n{upstream_tls}        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto $scheme;\n        proxy_connect_timeout 5s;\n        proxy_read_timeout 60s;\n    }}\n}}\n"""


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
            client.settimeout(10)
            client.connect(str(SOCKET_PATH))
            client.sendall(request)
            response = json.loads(client.recv(4096).decode("utf-8"))
    except (OSError, ValueError) as exc:
        return OperationResult(False, f"Privileged service request failed: {exc}")
    return OperationResult(bool(response.get("ok")), response.get("message", "Operation failed."))


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
