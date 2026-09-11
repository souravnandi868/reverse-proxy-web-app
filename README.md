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

## Public IP and incoming traffic

Public IP is resolved from the FQDN on each proxy save, including in Django admin. It is not a manually editable field. To refresh existing records, run `python manage.py refresh_proxy_metadata --all`.

The Incoming Traffic page shows the connecting client IP, destination FQDN, actual upstream server address and port, request, status, and timing from NGINX access logs.

Before starting the operations helper on Linux, install the logging format and managed-route include (assuming the app is installed in `/opt/proxy-admin`):

```sh
install -d -o root -g nginx -m 0750 /etc/nginx/conf.d/proxy-admin /var/log/nginx/proxy-admin /run/nginx-proxy-admin
install -o root -g root -m 0644 /opt/proxy-admin/deploy/00-proxy-admin-logging.conf /etc/nginx/conf.d/00-proxy-admin-logging.conf
/usr/sbin/nginx -t
systemctl reload nginx
```

The main NGINX `http` block must include `/etc/nginx/conf.d/*.conf`. Give the Django service account membership in the `nginx` group and read access to the access logs, including after log rotation. Set `NGINX_ACCESS_LOG_DIR` if logs are stored elsewhere. The incoming IP is the direct peer seen by NGINX; if a trusted load balancer sits in front, configure NGINX real-IP handling for that balancer to record the original client.
