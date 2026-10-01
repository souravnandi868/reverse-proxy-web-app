# NGINX Proxy Control

### Console appearance and updates

The console opens in a dark navy/cyan theme. Use the theme button beside search
to switch to light mode; the preference is saved in the current browser. All
fonts, icons, scripts and styles work without external asset services.
Monitoring updates and form operations preserve scroll position. Certificate
expiry badges show remaining days: yellow at 15–30 days, red below 15 days,
and a separate expired message for past dates. Settings is last in the sidebar.

When deploying a new release, run migrations and `collectstatic --noinput`,
restart Django and the traffic collector, then hard-refresh the browser.

### Incoming Traffic date-range exports

Enter export start and end times in IST (UTC+05:30). The export includes both
endpoints, respects the selected FQDN, and contains only retained requests.
If no requests match, the page displays an explanation instead of downloading
an empty workbook. Older rows without an indexed timestamp use their original,
timezone-aware log timestamp for export filtering.

After deploying collector or traffic model updates, restart both the Django
application and `proxy-traffic-collector` using the deployment account and the
existing service procedure. A collector left running old code can continue
writing requests without their indexed timestamps. On installations using the
provided systemd unit, restart it with `sudo systemctl restart proxy-traffic-collector`.

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


## Website Usage graphs

The staff-only **Website Usage** page (`/usage/`) compares FQDNs and shows their configured backend addresses. Select all websites or a single FQDN and a 3-, 5-, or 18-minute window. Traffic RX/TX rates, completed request counts, HTTP 4xx/5xx errors, and average NGINX request duration are graphed in 10-second buckets; the page refreshes every 3 seconds. Hover over graph points for values. Table totals cover the selected range. NGINX request duration includes client transfer time and is not backend-only latency.

The existing traffic collector supplies this page, independently of open browsers. Migration 0011 indexes request timestamps and backfills valid timezone-aware timestamps in retained records. The collector now preserves `received_bytes` and `sent_bytes` from the existing managed NGINX log format. Previously discarded byte fields cannot be recovered by this migration: incomplete byte totals display Unavailable until new records cover the selected window. No body-byte fallback is used. Empty or missing measurements are not proof that a server is idle; collector delays, downtime and the 100,000-request retention limit can reduce coverage. These charts do not measure CPU, RAM or disk per FQDN/backend; that requires separate monitoring.

Deploy the full updated application, run `.venv/bin/python manage.py migrate` and `.venv/bin/python manage.py collectstatic --noinput`, restart the Django service, and restart `proxy-traffic-collector` on the collector host after deploying its updated code there. Use Ctrl+Shift+R to load the new sidebar and scripts. No resource-agent change is required for this page. Existing installations already using the managed byte logging format do not require an NGINX reload.
# Optional OTP captive portal

Each HTTPS proxy has an **Enable OTP captive portal** checkbox, disabled by default.
Saving stages the change; use the existing **Apply** action to activate it. Routes
without the checkbox retain their previous generated NGINX configuration byte for
byte. Captive routes retain their certificates, upstream headers, timeouts and
optional WebSocket configuration. HTTP captive routes are rejected because the
required Secure cookie needs HTTPS.

## Authorized users and permissions

Use **Authorized Users** in the console (also linked from Django admin) to register,
search, edit, enable/disable, assign FQDNs or confirm deletion. Name, section and rank
are required. Both mobile number and email address are mandatory. Indian mobile
numbers are normalized to `+91` plus ten digits; email addresses are normalized
to lowercase. Both are unique. Users may identify with either registered value,
but OTP delivery always goes to the registered mobile by SMS. Explicit **all
captive-enabled FQDNs** access is separate from an empty assignment list; an
empty list grants no access. Self-registration is not enabled.

Operators must be authenticated staff and have the relevant `proxies` permissions:
`view_captiveportaluser`, `add_captiveportaluser`, `change_captiveportaluser`,
`toggle_captiveportaluser`, `delete_captiveportaluser`, and
`assign_captiveportaluser`. Assign `view_captiveaudit` to view/search captive audit
events. Editing permission alone cannot enable users or change their assignments.
Superusers have all permissions. Grant permissions with the existing Django admin
user/group management. User deletion requires confirmation, clears contact details,
soft-deletes the record and retains security audit relationships. Disable, contact
changes and deletion revoke sessions and outstanding OTPs; removing assignments
revokes access for those FQDNs. Re-enabling never restores old sessions.

## Production environment

Set these in the existing service environment, never in source control. No new
production Python dependencies are required.

| Variable | Required value / default |
| --- | --- |
| `DJANGO_SECRET_KEY` | A unique high-entropy production secret shared by this application's workers; never the development default |
| `DJANGO_DEBUG` | `false` in production |
| `DJANGO_ALLOWED_HOSTS` | Explicit console hostname **and every captive FQDN**, comma-separated; no wildcard |
| `CAPTIVE_ADMIN_UPSTREAM` | Private Django listener; default `http://127.0.0.1:8000`; only loopback HTTP URLs are accepted |
| `CAPTIVE_SMS_BACKEND` | `http` in production; default `disabled` fails closed; `development` silently discards delivery and requires DEBUG |
| `CAPTIVE_SMS_API_URL` | `https://api.kolkatapolice.org/crimebabuapp/Api_sms/send_sms` |
| `CAPTIVE_SMS_HTTP_ADAPTER` | `proxies.captive_sms.GatewayAdapter` (default) |
| `CAPTIVE_SMS_MESSAGE_TEMPLATE` | `{#var#} is your OTP to access the zimbra mail in your device - Kolkata Police` |
| `CAPTIVE_OTP_EXPIRY_SECONDS` | `300` (5 minutes) |
| `CAPTIVE_SESSION_SECONDS` | `28800` (8 hours, absolute expiry) |
| `CAPTIVE_RESEND_SECONDS` | `60` |
| `CAPTIVE_MAX_ATTEMPTS` | `5` per issued OTP |
| `CAPTIVE_DESTINATION_SEND_LIMIT` | `5` per hour per registered mobile number |
| `CAPTIVE_IP_SEND_LIMIT` | `20` per hour, including unknown destinations |
| `CAPTIVE_USER_SEND_LIMIT` | `10` per hour per authorized user account |
| `CAPTIVE_FQDN_SEND_LIMIT` | `1000` per hour |
| `CAPTIVE_VERIFY_IP_LIMIT` | `100` verification attempts per hour |
Email is only an alternative login identifier; captive authentication never sends
email. The development SMS provider never contacts a gateway or records the OTP;
tests mock SMS delivery.

### SMS gateway request

authentication format and accepted response are not documented here. The base
and any IP allowlisting requirements. These define the **exact request format to
The adapter sends an unauthenticated HTTPS `POST` with
`Content-Type: application/x-www-form-urlencoded` and fields `mobileno` and
`message`. It strips `+91` from the internally normalized Indian mobile number to
match the provider's 10-digit example. The message replaces `{#var#}` in
`CAPTIVE_SMS_MESSAGE_TEMPLATE` with the six-digit OTP. A live provider test returned
HTTP 200 and JSON `{"status":1,"message":"Success"}`; only that successful
response is accepted. The provider's observed error response uses status `0`.

No API token, sender ID or template ID is sent or required by this endpoint. Set
`CAPTIVE_SMS_BACKEND=http` and the endpoint in the service environment. The transport
verifies HTTPS, uses 5-second connect and 10-second response timeouts, limits
responses to 64 KiB, and follows no redirects. Failures consume the challenge and
cannot establish a session. Gateway exception messages, request bodies and response
bodies are not logged. Disable payload logging on the gateway side too.

## NGINX and session flow

NGINX must include `--with-http_auth_request_module` (`nginx -V`). As documented in
the [NGINX auth_request reference](https://nginx.org/en/docs/http/ngx_http_auth_request_module.html),
a successful subrequest permits access and a 401/403 denies access.

The generator reserves exact same-FQDN paths `/_captive/login/`, `send-otp/`,
`verify-otp/`, `logout/`, and `status/`. They go to the loopback Django listener and
bypass backend authentication. All other `/_captive/` paths are rejected. An
`internal` `/_captive_auth` location calls Django's lightweight session check.
An internal named error handler asks Django to return either a safely encoded
local login redirect or a JSON 401 when Accept requests application/json. Django
rejects external, protocol-relative, backslash, control-character and portal-loop
return destinations. Paths and query strings survive successful login. NGINX checks
the WebSocket handshake before forwarding an Upgrade; it cannot revoke an already
established WebSocket stream until that connection closes.

NGINX supplies a per-FQDN keyed ingress header and overwrites the client-IP header;
Django rejects unsigned ingress, forged/unlisted hosts and disabled/non-HTTPS
routes. Keep Gunicorn bound to loopback and block direct public access. The console
NGINX vhost must not expose `/_captive/` paths or forward arbitrary captive ingress
headers. Do not enable general `USE_X_FORWARDED_HOST` or trust arbitrary forwarded
client-IP headers. If a trusted load balancer precedes NGINX, configure NGINX real-IP
trust explicitly so `$remote_addr` reflects the client, not a spoofable header.

The ingress key is derived from `DJANGO_SECRET_KEY`; generated configs and backups
must remain private. After rotating that secret, regenerate/apply all captive
routes together with restarting Django workers. Existing challenges and sessions
become invalid. Ensure every worker/replica uses the same secret and an authoritative
session/rate-limit database. SQLite works across workers on one host; asynchronously
copied SQLite files are not a shared rate-limit or revocation store across active
hosts. Existing backup/replication jobs are unchanged; do not enable active-active
captive ingress against independently writable database copies.

OTP challenges contain keyed SHA-256 hashes, never plaintext codes. Six digits are
generated with Python `secrets`; comparison is constant-time, attempts are reserved
atomically, and successful verification consumes the challenge. New codes consume
previous unused codes for that user/FQDN/channel. Independent database counters
enforce the configurable send and verification limits across Gunicorn workers.
Public send responses are generic for unknown, disabled, deleted and unassigned
contacts. Only the selected registered channel receives delivery.

Sessions use a new random identifier after verification, store only its keyed hash,
and bind to the user and proxy. Every check revalidates current authorization and
absolute expiry. The host-only `__Host-captive_session` cookie has Secure, HttpOnly,
SameSite=Lax, Path=/ and no Domain. IP and User-Agent are audit context, not access
credentials. Logout is a CSRF-protected POST that revokes the server record and
expires the cookie. Captive pages and auth responses use no-store. The session
cookie is stripped before forwarding to the backend, while backend cookies remain.

Captive route traffic logs retain the collector's byte/timing fields but omit query
strings, cookies, authorization and user-agent fields. Portal/auth locations have
access logging disabled. Existing non-captive log formats are unchanged. Configure
the Django/Gunicorn frontend and observability agents to avoid request bodies,
cookies and credential headers; disable access logging for captive endpoints. Keep
security audits under the organization's retention policy; no automatic audit
deletion is performed by this feature.

See `deploy/captive-example.conf` for a full generated configuration with a redacted
ingress key. The same privileged operations service writes the configuration,
executes `nginx -t`, and reloads only on success. Validation failure restores the
previous file without reload. This feature does not restart NGINX.

## Offline deployment and rollback

1. Back up the application database, private media and current managed NGINX files.
   Transfer the reviewed source, adapter and existing offline dependency wheelhouse
   to the offline host. Install from the wheelhouse using the deployment's existing
   virtualenv (`pip install --no-index --find-links /path/to/wheels -r requirements.txt`).
2. Provision the environment above in the existing private service environment.
   Ensure every captive FQDN has a certificate and is explicitly allowed by Django.
   Confirm the existing NGINX build includes auth_request and the local Django
   listener is reachable. SMS/SMTP still require access to the organization's
   approved SMS gateway; an offline package install does not itself provide
   message delivery.
3. With the existing Django service account/environment, run:

   ```sh
   python manage.py migrate --noinput
   python manage.py check
   python manage.py collectstatic --noinput
   ```

   Migration `0013_captive_portal` adds the false-default route flag, captive
   models, assignments and constraints. Migration `0014_captive_sms_identifier`
   disables legacy authorized users missing either required contact. Existing
   routes remain non-captive. Use the deployment's existing graceful Django
   code-reload procedure.
4. Grant administrator permissions, register users, assign FQDNs, configure/test
   the SMS provider settings, then enable and Apply one pilot route. Verify a fresh
   browser session, email/SMS, logout, JSON access and WebSocket handshake before
   rolling out the remaining routes. Apply uses the existing privileged socket.
   Run deployment validation on the actual Linux NGINX build as well.
5. To roll back portal behavior, clear its checkbox and Apply each affected route.
   This deliberately restores unrestricted backend access on those routes, so
   coordinate the access-policy change. For a full code rollback, restore/reapply
   the pre-feature NGINX backups before removing code that serves auth endpoints.
   Retain migration 0013/data for security history where possible. Reversing it
   (`python manage.py migrate proxies 0012`) permanently drops captive users,
   assignments, sessions and audits; do so only after an approved database backup
   and retention decision. Validation failures automatically restore the old file;
   investigate helper-reported restoration failures before proceeding.

## Captive verification

```sh
python manage.py test --noinput
node --test tests/*.test.cjs
python tests/nginx_captive_integration.py /path/to/nginx
# With Playwright and Chrome installed, also test the responsive portal in Chrome:
python tests/nginx_captive_integration.py /path/to/nginx --browser
# Optional existing UI integration suite: install Playwright and Chrome first.
python manage.py test tests.browser_navigation --noinput
```

The NGINX smoke test uses an isolated database, temporary TLS certificate, loopback
servers and mocked delivery. It validates generated syntax and exercises CSRF,
OTP, redirects/query preservation, JSON 401, cookie stripping, internal locations,
WebSocket Upgrade gating and logout. It does not contact a gateway or alter an
installed NGINX service. The migration test upgrades existing data from 0012,
checks the disabled default, disables legacy single-contact users, and verifies
reversal. In a Windows sandbox that
cannot spawn Node workers, use `node --test --test-isolation=none tests/*.test.cjs`.
