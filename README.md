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

## NGINX reverse proxy host monitoring

Open **NGINX Server** for CPU, RAM, storage by mount, and network RX/TX graphs. The page polls every 5 seconds and keeps the last 60 distinct samples in the browser. Only the latest sample is stored in the database; this is not historical monitoring. A sample older than 30 seconds is marked stale, not offline. Only the explicitly enrolled NGINX host is shown. Backend routes never create monitoring targets.

Install the agent only on the Oracle Linux 9.5 host running NGINX. No software is required on hosted/backend servers. No inbound agent port is needed. The agent sends metrics to the Django application over HTTPS using the enrolled NGINX host token. The IP identifies the server and does not need to match the outbound NAT address.

After pulling this update on the app server:

```sh
source .venv/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py enroll_monitor YOUR_NGINX_SERVER_IP
```

Restart your Django service. Save the generated token securely; rerunning enrollment rotates it. To revoke an agent, delete its ServerMonitor record using the Django shell or rotate its token. Enrollment selects this IP as the reverse proxy host and disables previous monitoring targets without deleting their stored records. After upgrading from backend monitoring, run enrollment again with the NGINX host IP and update its agent token.

### Oracle Linux 9.5 agent installation

On the NGINX server, copy `ops/monitor-agent.py`, `ops/monitor-requirements.txt`, and `deploy/proxy-monitor-agent.service` from this repository. From that checkout:

```sh
sudo dnf install -y python3.11 python3.11-pip
sudo install -d -m 0755 /opt/proxy-monitor
sudo install -m 0644 ops/monitor-agent.py ops/monitor-requirements.txt /opt/proxy-monitor/
sudo python3.11 -m venv /opt/proxy-monitor/.venv
sudo /opt/proxy-monitor/.venv/bin/pip install -r /opt/proxy-monitor/monitor-requirements.txt
sudo install -m 0644 deploy/proxy-monitor-agent.service /etc/systemd/system/
sudo touch /etc/proxy-monitor-agent.env
sudo chmod 600 /etc/proxy-monitor-agent.env
sudo vi /etc/proxy-monitor-agent.env
```

Set these values in that root-readable environment file:

```ini
MONITOR_URL=https://YOUR-ADMIN-DOMAIN/monitor/ingest/
MONITOR_ADDRESS=YOUR_NGINX_SERVER_IP
MONITOR_TOKEN=TOKEN_FROM_ENROLLMENT
```

Use the application's HTTPS address, not a proxied backend domain. The certificate must be trusted by Python. For a private CA, set `SSL_CERT_FILE` to the CA bundle in the same environment file. Preserve the trailing slash; the agent deliberately refuses redirects. NGINX must forward `/monitor/ingest/` and its Authorization header to Django. Keep the endpoint request body limit at least 64 KiB.

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now proxy-monitor-agent
sudo journalctl -u proxy-monitor-agent -n 30 --no-pager
```

The agent runs unprivileged. Only readable, mounted filesystems are reported. Network rates use counter differences over elapsed time; per-interface utilization is the larger of RX/TX divided by the reported link speed, not Internet bandwidth. Unknown link speeds are labeled accordingly. Aggregated traffic can count the same packet on bridges or virtual interfaces; use the per-interface details for diagnosis.

These measurements describe the whole NGINX host, including all processes and interface traffic; they are not NGINX-process-only metrics. The Live label means the resource agent is reporting, not that the NGINX service has passed a health check.

Metric collection follows the [psutil documentation](https://psutil.readthedocs.io/stable/).
