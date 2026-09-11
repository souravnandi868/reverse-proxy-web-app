#!/usr/bin/env python3
"""Root-owned helper: accepts only structured, allowlisted operations over a Unix socket."""
import json
import os
import re
import socket
import subprocess
import tempfile
from pathlib import Path

SOCKET = Path("/run/nginx-proxy-admin/ops.sock")
NGINX_DIR = Path("/etc/nginx/conf.d/proxy-admin")
LOG_DIR = Path("/var/log/nginx/proxy-admin")


def reply(conn, ok, message):
    conn.sendall(json.dumps({"ok": ok, "message": message}).encode())


def write_config(target, content):
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=NGINX_DIR, delete=False) as temp:
            temp_name = temp.name
            temp.write(content)
        os.chmod(temp_name, 0o640)
        os.replace(temp_name, target)
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)


def handle(request):
    action = request.get("action")
    payload = request.get("payload", {})
    domain = payload.get("domain", "")
    if (action not in {"apply", "rollback"} or not isinstance(domain, str)
            or len(domain) > 253 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", domain)
            or ".." in domain):
        return False, "Rejected operation."
    config = payload.get("config", "")
    if not isinstance(config, str) or not config.startswith("# Managed by NGINX Proxy Admin.") or len(config) > 100000:
        return False, "Rejected configuration payload."
    NGINX_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
    target = NGINX_DIR / f"{domain}.conf"
    if target.is_symlink() or target.resolve().parent != NGINX_DIR.resolve():
        return False, "Rejected managed configuration path."
    previous = target.read_bytes() if target.exists() else None
    removing = action == "apply" and payload.get("enabled", True) is False
    failure = "Operation failed; managed configuration restored."
    try:
        if removing:
            target.unlink(missing_ok=True)
        else:
            write_config(target, config.encode())
        LOG_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
        test = subprocess.run(["/usr/sbin/nginx", "-t"], capture_output=True, text=True, timeout=15)
        if test.returncode != 0:
            failure = "NGINX validation failed; no reload was performed."
            raise RuntimeError(failure)
        reload_result = subprocess.run(["/usr/bin/systemctl", "reload", "nginx"], capture_output=True, text=True, timeout=15)
        if reload_result.returncode != 0:
            failure = "NGINX reload failed; managed configuration restored."
            raise RuntimeError(failure)
    except Exception:
        try:
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                write_config(target, previous)
        except Exception:
            return False, "Operation failed and configuration restoration failed; operator recovery required."
        return False, failure
    return True, "Managed configuration removed; NGINX validated and reloaded." if removing else "NGINX configuration validated and reloaded."


def main():
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


if __name__ == "__main__":
    main()
