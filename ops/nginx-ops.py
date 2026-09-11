#!/usr/bin/env python3
"""Root-owned helper: accepts only structured, allowlisted operations over a Unix socket."""
import json
import os
import socket
import subprocess
import tempfile
from pathlib import Path

SOCKET = Path("/run/nginx-proxy-admin/ops.sock")
NGINX_DIR = Path("/etc/nginx/conf.d/proxy-admin")
LOG_DIR = Path("/var/log/nginx/proxy-admin")
LOG_FORMAT_CONFIG = Path("/etc/nginx/conf.d/00-proxy-admin-logging.conf")
LOG_FORMAT = "log_format proxy_admin escape=json '{\\\"time\\\":\\\"$time_iso8601\\\",\\\"source_ip\\\":\\\"$remote_addr\\\",\\\"destination_fqdn\\\":\\\"$host\\\",\\\"destination_server\\\":\\\"$upstream_addr\\\",\\\"request\\\":\\\"$request\\\",\\\"status\\\":$status,\\\"bytes\\\":$body_bytes_sent,\\\"request_time\\\":$request_time,\\\"user_agent\\\":\\\"$http_user_agent\\\"}';\n"


def reply(conn, ok, message):
    conn.sendall(json.dumps({"ok": ok, "message": message}).encode())


def handle(request):
    action = request.get("action")
    payload = request.get("payload", {})
    domain = payload.get("domain", "")
    if action not in {"apply", "rollback"} or not domain or "/" in domain or ".." in domain:
        return False, "Rejected operation."
    config = payload.get("config", "")
    if not config.startswith("# Managed by NGINX Proxy Admin.") or len(config) > 100000:
        return False, "Rejected configuration payload."
    NGINX_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
    target = NGINX_DIR / f"{domain}.conf"
    previous = target.read_bytes() if target.exists() else None
    if action == "apply" and not payload.get("enabled", True):
        target.unlink(missing_ok=True)
    else:
        with tempfile.NamedTemporaryFile("w", dir=NGINX_DIR, delete=False) as temp:
            temp.write(config)
            temp_name = temp.name
        os.chmod(temp_name, 0o640)
        os.replace(temp_name, target)
    LOG_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
    LOG_FORMAT_CONFIG.write_text(LOG_FORMAT)
    test = subprocess.run(["/usr/sbin/nginx", "-t"], capture_output=True, text=True, timeout=15)
    if test.returncode != 0:
        if previous is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(previous)
        return False, "NGINX validation failed; no reload was performed."
    reload_result = subprocess.run(["/usr/bin/systemctl", "reload", "nginx"], capture_output=True, text=True, timeout=15)
    if reload_result.returncode != 0:
        return False, "NGINX validated but reload failed."
    return True, "NGINX configuration validated and reloaded."


SOCKET.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
SOCKET.unlink(missing_ok=True)
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
    server.bind(str(SOCKET))
    os.chmod(SOCKET, 0o660)
    server.listen(5)
    while True:
        conn, _ = server.accept()
        with conn:
            try:
                reply(conn, *handle(json.loads(conn.recv(200000).decode())))
            except Exception:
                reply(conn, False, "Operation failed safely.")
