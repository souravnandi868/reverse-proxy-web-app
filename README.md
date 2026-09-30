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

## Incremental incoming-traffic collection

Run `python manage.py collect_traffic` continuously alongside Django. It reads appended JSON log lines every 2 seconds; the Incoming Traffic page refreshes every 3 seconds. Run the collector where it can read `NGINX_ACCESS_LOG_DIR` and connect to the same database as Django. On separate hosts, provide read-only access to those logs on the collector host; the resource agent does not forward individual requests.

The collector persists device/inode identity, byte offsets, and a 64-byte continuity marker in `TrafficCursor`. Cursor advances and event inserts commit together, so restarts resume without replaying committed events. Renamed `*.access.log*` files remain discoverable for late writes; compressed archives are ignored. Configure rotation to retain uncompressed rotated files long enough to drain them (for example, delayed compression). Truncation or a changed continuity marker resets that file's offset. As with other file tailers, copytruncate can lose writes during truncation; rename-and-reopen rotation is preferred. Deleting/compressing unread logs loses those unread events.

On first discovery, only the final 256 KiB of an existing file is read; older content is not imported. Each cycle reads at most 256 KiB per file and approximately 4 MiB overall, plus bounded continuity checks. A busy collector catches up over later cycles, so sustained input above this budget delays live data. Lines over 16 KiB and malformed JSON are skipped. Symlinks and non-regular files are ignored. Memory keeps the last 1,000 events in a deque; database history retains at most 100,000 events after each cycle. Unseen cursor records expire after seven days. History is a rolling operational view, not a permanent compliance archive.

Dashboard, traffic refresh, history pagination, and Excel export query indexed events only; they never open access logs. History uses 100-row keyset pages, so incoming inserts do not shift older pages. Historical pages do not auto-refresh; use **Back to live traffic** to resume. Excel exports retained indexed history, including collected rotated-file entries, rather than all contents of the original logs. Arbitrary log fields (including authorization/token fields) are not stored. Existing staff authorization and HTML escaping still apply. Browser pollers allow only one outstanding request per view and abort it on navigation.

For an existing installation, deploy the application code, run `python manage.py migrate --noinput` (adds the cursor/event tables), and `python manage.py collectstatic --noinput` using the Django service account and environment. Use the existing application's graceful code-reload procedure. Review the account, project/virtualenv paths, and environment in `deploy/proxy-traffic-collector.service` before installing it:

```sh
sudo install -m 0644 deploy/proxy-traffic-collector.service /etc/systemd/system/proxy-traffic-collector.service
sudo systemctl daemon-reload
sudo systemctl enable --now proxy-traffic-collector
sudo systemctl is-active proxy-traffic-collector
sudo journalctl -u proxy-traffic-collector -n 30 --no-pager
```

The service account needs log read access and write access to the Django database and project lock file `.traffic-collector.lock`. Exactly one collector must run per installation/log directory; an OS lock rejects a second worker on the same host. Do not run collectors on multiple hosts against the same log source. `python manage.py collect_traffic --once` performs one bounded collection pass when the continuous worker is stopped.

This rollout adds a background collector; an agent-only restart cannot enable it. It requires no NGINX reload/restart, credential rotation, certificate change, or backend application restart. For resource collection, copy the revised agent to `/opt/proxy-monitor/monitor-agent.py` using the agent-update procedure below. The default is now 3 seconds; any explicitly configured `MONITOR_INTERVAL` overrides it, so existing 30-second configurations need an operator change to 3 to meet that cadence. Never print the environment file or token. No production changes are made by the tests.

Verification:

```sh
python manage.py test --noinput
python -m unittest proxies.test_monitor_interval -v
node --test tests/polling.test.cjs tests/servers.test.cjs tests/search.test.cjs
```

The performance regression creates sparse 1 MiB and 500 MiB log files, then appends the same 50 records. It asserts identical bytes read and exactly 50 JSON parses for both, bounded bootstrap/idle reads, and a generous elapsed-time bound. It also covers restart recovery, rotation, copytruncate, partial/oversized lines, retention, atomic checkpoints, pagination, single-worker locking, and HTTP reads without log access.

## NGINX reverse proxy host monitoring

Open **NGINX Server** for CPU, RAM, total file size inside `/var/log/nginx/`, and the **External NGINX Traffic** RX/TX graph. The page polls every 3 seconds and loads the latest 360 readings saved in the database (about 18 minutes at the default agent interval). Readings are retained while the page is closed and survive navigation, refreshes, and application restarts. Reporting gaps longer than 30 seconds break graph lines. History starts accumulating after this update; previously discarded readings cannot be recovered. Deploy migration 0010 with `python manage.py migrate`, refresh static assets with `python manage.py collectstatic --noinput`, and restart the Django service. Update the monitoring agent using the procedure below and set MONITOR_INTERVAL=3 in its existing environment file to collect new readings every three seconds. History collection continues while the page is closed. A sample older than 30 seconds is marked stale, not offline. Only the explicitly enrolled NGINX host is shown. Backend routes never create monitoring targets.

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
MONITOR_INTERVAL=3
```

Use the application's HTTPS address, not a proxied backend domain. The certificate must be trusted by Python. For a private CA, set `SSL_CERT_FILE` to the CA bundle in the same environment file. Preserve the trailing slash; the agent deliberately refuses redirects. NGINX must forward `/monitor/ingest/` and its Authorization header to Django. Keep the endpoint request body limit at least 64 KiB.

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now proxy-monitor-agent
sudo journalctl -u proxy-monitor-agent -n 30 --no-pager
```

The agent runs unprivileged. Storage recursively sums regular file sizes inside `/var/log/nginx/`, including rotated logs and subdirectories. It excludes symbolic links and counts hard-linked files once. This is logical file size, not allocated disk blocks or filesystem capacity; no free-space or percentage meter is shown. Empty directories report zero; missing or inaccessible directories report unavailable. The agent needs directory read/traverse permissions. Older filesystem-only samples are shown as unavailable until the agent is updated. After updating the app, copy the updated `ops/monitor-agent.py` to `/opt/proxy-monitor/monitor-agent.py` and restart `proxy-monitor-agent`. Network interfaces remain visible when down, with explicit link state. Newly detected interfaces show a waiting message until a second counter sample is available. Older agents show unknown link state until updated. Diagnostic NIC rates use counter differences over elapsed time; per-interface utilization is the larger of RX/TX divided by the reported link speed, not Internet bandwidth. Unknown link speeds are labeled accordingly. Aggregated traffic can count the same packet on bridges or virtual interfaces; use the per-interface details for diagnosis.

**External NGINX Traffic** uses only newly appended JSON entries in `/var/log/nginx/proxy-admin/*.access.log`. The `proxy_admin` format records `received_bytes` from `$request_length` and `sent_bytes` from `$bytes_sent`; the agent submits their interval rates as `nginx_rx_bps` and `nginx_tx_bps`. The graph shows KiB/s or MiB/s and never falls back to NIC counters. These are completed-request HTTP bytes, including headers, not wire bandwidth: TLS/TCP overhead is excluded, and streaming requests appear when their access entry is written. Physical and virtual NIC counters remain in the separate host diagnostic section.

The reader starts at EOF on every agent restart (traffic while stopped is intentionally not replayed), reads new files from the beginning, and keeps renamed file handles until they have been inactive for 60 seconds to drain rotation writes. Truncation resets the offset; a trailing-content check also detects truncation followed by regrowth. Prefer rename/reopen rotation: copytruncate can lose bytes during the copy/truncate race, which no polling reader can recover. Partial lines wait for completion; malformed, oversized, and old-format entries are skipped. Missing or unreadable log directories report unavailable rather than NIC traffic.

To deploy this traffic update, install the updated `deploy/00-proxy-admin-logging.conf` in `/etc/nginx/conf.d/`, run `sudo nginx -t`, and reload NGINX only after validation succeeds. Deploy the app and run `python manage.py collectstatic --noinput`. Install the updated agent and systemd service, then run `sudo systemctl daemon-reload` and `sudo systemctl restart proxy-monitor-agent`. The service uses the `nginx` supplementary group: ensure that group can traverse the log directories and read every managed access log, including files created by log rotation (configure the rotation owner/group and mode accordingly). The resource agent alone needs no enrollment change. The incremental incoming-traffic collector described below requires migration 0008.

CPU, RAM and NIC diagnostics describe the whole NGINX host. The Live label means the resource agent is reporting, not that the NGINX service has passed a health check.

Metric collection follows the [psutil documentation](https://psutil.readthedocs.io/stable/).

## Automatic DNS refresh every five minutes

Install the supplied systemd timer on the Django app server. It checks every saved FQDN (including disabled routes) every five minutes, even when nobody has the dashboard open. Only changed public IPs are written; proxy configuration timestamps and NGINX configuration are not changed. Failed lookups keep the last known public IP and produce a journal warning. DNS resolver caching/TTL still applies. Reload an open dashboard or Domains & IPs page to see the latest saved IP.

From the project directory on Oracle Linux 9.5:

```sh
sudo install -m 0644 deploy/proxy-dns-refresh.service deploy/proxy-dns-refresh.timer /etc/systemd/system/
sudo vi /etc/systemd/system/proxy-dns-refresh.service
```

Before enabling, match `User`, `Group`, `WorkingDirectory`, `ExecStart`, and `EnvironmentFile` to your Django deployment. The example uses account `nginxproxy`, project `/opt/proxy-admin`, and virtualenv `.venv`. If your virtualenv is `venv`, change the executable to `/opt/proxy-admin/venv/bin/python`. Use the same database settings/environment as Django and an account able to write its database. Do not create a separate database for the timer.

```sh
sudo systemctl daemon-reload
sudo systemctl start proxy-dns-refresh.service
sudo systemctl enable --now proxy-dns-refresh.timer
sudo systemctl list-timers proxy-dns-refresh.timer
sudo journalctl -u proxy-dns-refresh.service -n 30 --no-pager
```

For an immediate manual refresh in the application's virtualenv:

```sh
python manage.py refresh_proxy_metadata --all --dns-only
```

## Deploy an agent interval update on an existing installation

`MONITOR_INTERVAL=3` sets the delay between submissions in seconds and is also the default when unset. The value must be an integer from 3 through 3600 inclusive; invalid values terminate the agent at startup. Collection and request processing add to this delay. The console's existing 30-second stale threshold is unchanged, so samples can be marked stale between submissions.

From the updated repository checkout on the monitoring host, run the following agent-only update. It preserves the existing environment file, credentials, and service definition. An existing `MONITOR_INTERVAL` setting takes precedence over the 3-second default.

```sh
set -eu
sudo /opt/proxy-monitor/.venv/bin/python -c 'import ast; from pathlib import Path; ast.parse(Path("ops/monitor-agent.py").read_text())'
sudo cp -p /opt/proxy-monitor/monitor-agent.py /opt/proxy-monitor/monitor-agent.py.bak
sudo install -m 0644 ops/monitor-agent.py /opt/proxy-monitor/monitor-agent.py.new
sudo mv /opt/proxy-monitor/monitor-agent.py.new /opt/proxy-monitor/monitor-agent.py
sudo systemctl restart proxy-monitor-agent
sudo systemctl is-active proxy-monitor-agent
sudo journalctl -u proxy-monitor-agent -n 30 --no-pager
```

Allow at least one configured interval for a new sample to reach the console. This update restarts only `proxy-monitor-agent`; no NGINX reload/restart, Django restart, database migration, or `/etc` edit is needed. Hosted web applications continue running. For rollback, restore `/opt/proxy-monitor/monitor-agent.py.bak` to `/opt/proxy-monitor/monitor-agent.py` and restart only `proxy-monitor-agent`.

## Proxy deletion safety

Only authenticated superusers can delete a proxy, using the CSRF-protected POST confirmation page. Django admin deletion is disabled to prevent bypassing this workflow. GET only displays the exact domain and backend with a route-removal warning.

Deletion passes a temporarily disabled in-memory proxy to the restricted Unix-socket helper; the saved enabled flag is never temporarily changed. The helper removes only the managed domain configuration, validates Nginx, and reloads it before acknowledging success. It restores the previous managed file on validation/reload failure or an exception. Certificate bundles and PEM files (including shared certificates), access/error logs, DNS, firewall/NAT configuration, and backend applications are untouched.

After acknowledgement, a short database transaction checks for intervening proxy changes and deletes the proxy together with an independent audit event containing only domain, public IP, backend IP/port, incoming/backend protocols, and the original enabled flag. ConfigurationBackup records cascade with their proxy; the audit event survives. No database transaction is held while waiting for Nginx, including when ATOMIC_REQUESTS is enabled.

Nginx and the database cannot participate in a single atomic transaction. A lost acknowledgement, worker exit, concurrent edit, or database/audit failure after helper success can leave the route removed while the database record remains. The original saved enabled state is preserved; review and reapply the retained proxy or retry deletion to reconcile it. A reload timeout also leaves runtime state uncertain even when the previous managed file is restored. If filesystem restoration fails, operator recovery is required. The updated helper must accompany the application change to provide file restoration on reload failures; the socket request/response format is unchanged.


## Dynamic application views

The main application intercepts navigation and form submissions, rendering Django responses in place without reloading the browser document. Validation, CSRF protection, certificate deletion confirmation, browser Back/Forward, and PDF downloads are preserved. Lists receive background updates every five seconds, incoming traffic every three seconds, and server resources every three seconds; navigation stops the previous view's polling. Edit and upload fields are not replaced by background updates. Django admin and external links use normal navigation.

The optional browser regression test uses an isolated Django test database, mocks DNS and NGINX operations, and requires Google Chrome and Playwright (`python -m pip install playwright`). Run `python manage.py test tests.browser_navigation`. Runtime deployments do not need Playwright.
