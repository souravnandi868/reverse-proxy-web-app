# NGINX Proxy Control

A Django administration console for approved NGINX reverse proxy configurations on Oracle Linux 9.5.

## Security model

- Django runs as an unprivileged service account; it never runs shell commands and never receives sudo access.
- `ops/nginx-ops.py` is a root-owned, systemd-managed helper with a Unix socket protocol limited to `apply` and `rollback`.
- The helper writes only managed files, runs `/usr/sbin/nginx -t`, and reloads NGINX only after validation succeeds.
- Domain names, backend IP addresses, ports, protocols, and PEM uploads are validated server-side.
- Certificates live under private media storage and are not served by Django.
- Changes create database backups and audit events. Incoming traffic is assumed to arrive on the NGINX host; NAT/firewall notes are recorded only, and DNS and firewall APIs are intentionally not implemented.
- Each route has independent incoming and backend protocols. HTTPS can terminate at NGINX and proxy to the hosted server over HTTP, or NGINX can proxy to the hosted server over HTTPS.

## Local setup

```powershell
C:/Users/sourav-egov/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pip install -r requirements.txt
C:/Users/sourav-egov/AppData/Local/Python/pythoncore-3.14-64/python.exe manage.py migrate
C:/Users/sourav-egov/AppData/Local/Python/pythoncore-3.14-64/python.exe manage.py createsuperuser
C:/Users/sourav-egov/AppData/Local/Python/pythoncore-3.14-64/python.exe manage.py runserver
```

For production, set a strong `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_SECURE_SSL_REDIRECT=true`, configure PostgreSQL, run behind TLS, and install `deploy/nginx-proxy-ops.service` as root. Set `NGINX_OPS_SOCKET=/run/nginx-proxy-admin/ops.sock` if the socket location differs.
