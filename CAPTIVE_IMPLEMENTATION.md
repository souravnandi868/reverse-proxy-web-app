# Captive portal implementation

Implemented optional per-route SMS/email OTP, Authorized Users administration,
FQDN assignments, dedicated permissions, audit search, secure sessions and NGINX
auth_request integration. Existing non-captive configuration has an exact-output
regression test. No production dependencies were added.

## Migration

`proxies/migrations/0013_captive_portal.py` adds the disabled-by-default route flag,
CaptivePortalUser, CaptiveOTP, CaptiveSession, CaptiveAudit and CaptiveRateLimit,
FQDN assignments, contact constraints and management permissions. Tests exercise
0012 → 0013 with an existing route, then reverse and restore the migration.

Deployment command: `python manage.py migrate --noinput`.

## Files changed

New implementation:

* `proxies/captive.py`: trusted ingress, OTP, database rate limits, sessions, redirects.
* `proxies/captive_models.py`: captive data model and revocation hooks.
* `proxies/captive_admin.py`: authorized-user forms, permissions, actions and audit view.
* `proxies/captive_nginx.py`: portal/auth locations, private ingress, safe traffic format.
* `proxies/captive_sms.py`: SMS abstraction, HTTP transport and email delivery.
* `proxies/migrations/0013_captive_portal.py`.
* `templates/captive/login.html`, `users.html`, `user_form.html`, `user_action.html`,
  `audit.html`, `pagination.html`: public and console interfaces.
* `proxies/test_captive.py`, `proxies/test_captive_migration.py`.
* `tests/nginx_captive_integration.py`: isolated real NGINX/TLS/browser checks.
* `deploy/captive-example.conf`: generated example with redacted private ingress key.
* `CAPTIVE_IMPLEMENTATION.md`: this report.

Existing integration points:

* `proxies/models.py`, `forms.py`, `admin.py`, `services.py`, `urls.py`.
* `proxyadmin/settings.py`, `proxyadmin/urls.py`.
* `templates/base.html`, `templates/proxies/proxy_table.html`.
* `README.md`: environment, adapter contract, deployment, rollback and verification.
* `.gitignore`: isolated verification tools, scratch files and local test logs.
* `proxies/tests.py`, `proxies/test_traffic_reader.py`, `tests/browser_navigation.py`:
  refresh pre-existing stale fixtures for the current sidebar, required export range,
  CAPTCHA, switch dimensions, usage data, redirect destinations and mocked privileged operations. These changes do not
  change production proxy, authentication or traffic behavior.

## Verification

* Full Django suite: 150 tests passing.
* JavaScript suite: 16 tests passing (Node's in-process test mode avoids the Windows
  sandbox's child-process restriction).
* Existing Chrome workflow suite: all 6 tests passing together.
* Django system checks, migration drift check and `git diff --check`: passing.
* NGINX 1.30.5 Windows: generated configuration passes `nginx -t`.
* Real NGINX/TLS integration: browser redirect/query preservation, JSON 401,
  inaccessible internal auth, OTP/CSRF/session login, backend-cookie preservation,
  captive-cookie stripping, authenticated/unauthenticated WebSocket Upgrade and
  logout all pass.
* Mobile-width Chrome captive flow: no horizontal overflow, dark/light switch,
  disabled resend countdown, real form submission and OTP login pass.

The smoke test uses a temporary database/certificate and loopback backend, mocks
message delivery, and never modifies the installed NGINX service. Test tools were
installed only in the ignored `.test-tools` directory. Production Linux service
validation and actual gateway delivery remain deployment checks.

## Deployment references and remaining gateway input

The complete required environment table and offline/rollback steps are in
[README](README.md#production-environment). The generated configuration example is
[deploy/captive-example.conf](deploy/captive-example.conf).

Production SMS intentionally fails closed until the organization supplies its exact
URL, HTTP method, content type, payload fields, authentication format, sender/DLT
template substitutions, and success/failure response examples. Implement those in
the small configured `GatewayAdapter` contract; the transport does not guess them.
No real gateway credentials were used or committed.
