# Authentication security update

The console and captive portal now use layered database-backed rate limits. The
existing destination, user, IP, application and resend limits still govern SMS
issuance. A new layer runs before CAPTCHA validation and credential/OTP processing,
so incorrect CAPTCHA submissions also consume quotas. Counters are shared between
Django workers, increment atomically, and fail closed if their database is unavailable.
HTTP 429 responses include Retry-After; limiter database failures return HTTP 503.

| Control | Default per five minutes | Environment variable |
| --- | --- | --- |
| CAPTCHA page/reload per IP | 60 | AUTH_CAPTCHA_IP_LIMIT |
| CAPTCHA page/reload per browser session | 30 | AUTH_CAPTCHA_SESSION_LIMIT |
| Authentication POST per IP and endpoint | 30 | AUTH_POST_IP_LIMIT |
| Authentication POST per browser session and endpoint | 15 | AUTH_POST_SESSION_LIMIT |
| Passive fingerprint per operation | 120 | AUTH_FINGERPRINT_LIMIT |
| Console password submissions per normalized username | 10 | AUTH_USERNAME_LIMIT |

AUTH_RATE_WINDOW_SECONDS defaults to 300. Captive limits are scoped to each route;
console limits cover /login/ and /admin/login/. Passive fingerprints combine a
keyed hash of the browser agent and source network (/24 IPv4, /64 IPv6). Fingerprints
are spoofable signals, not proof of device identity. Independent IP, session,
username and existing destination quotas remain necessary. Users behind large NATs
may need limits tuned. Authentication does not trust arbitrary X-Forwarded-For.
If the console is behind a proxy, REMOTE_ADDR may be the shared proxy IP: tune
quotas and configure edge limits using the real remote address at trusted ingress.
Never accept client-supplied forwarding headers directly as rate-limit identity.

OTP challenges are bound to an unpredictable server-side browser-session identity
and browser agent. A copied challenge and code cannot authenticate a different
browser session. Existing OTP attempt reservation, expiry, hashed storage,
one-time consumption and authorization checks remain in place.

Captive sessions are bound to their browser agent and, by default, exact source IP.
Mismatch or 30 minutes without use revokes the session. Set
CAPTIVE_BIND_SESSION_IP=false only if network changes must be allowed; agent
binding remains. AUTH_SESSION_IDLE_SECONDS configures the idle timeout for both
console and captive sessions. SESSION_COOKIE_AGE defaults to eight hours and can
be changed through DJANGO_SESSION_COOKIE_AGE; captive absolute expiry continues
to use CAPTIVE_SESSION_SECONDS. Console sessions also expire on browser close.
A stolen cookie with identical agent/IP cannot be distinguished by these signals.

Management cookies use proxy_admin_session and proxy_admin_csrf to avoid collisions
with proxied applications. Captive ingress forces secure cookies independently of
the console TLS redirect setting. Generated captive NGINX routes strip the captive
session and both management cookies before forwarding to applications, retaining
application cookies. CSRF remains enforced on browser state-changing endpoints.
The monitor ingest endpoint deliberately uses bearer authentication, not cookies.

All Django responses receive a Content Security Policy: scripts require same-origin
files or the response nonce; inline handlers/eval, external script hosts, embedded
objects, foreign forms and framing are blocked. Captive inline scripts have explicit
nonces, and CAPTCHA reload handlers are registered by scripts. Style sources are
same-origin, with inline styles retained for the current console/admin layouts;
this is not a blanket prohibition on inline CSS. Images allow same-origin/data URLs
for CAPTCHA and the police emblem. Permissions-Policy disables unused device APIs,
and nosniff, frame denial, same-origin referrer policy and private no-store for
console/authentication pages are applied.

Generated captive NGINX public endpoints add per-IP request limits (2/second, burst
10) and connection limits (20), returning 429. Internal auth subrequests are not
throttled with the public form limits. These controls only take effect after each
managed captive route is reapplied and passes nginx -t.

## Deploy

1. Back up the application and database using the normal maintenance procedure.
2. Set a private DJANGO_SECRET_KEY and DJANGO_DEBUG=false. Set explicit
   DJANGO_ALLOWED_HOSTS. For the HTTPS console set DJANGO_SECURE_SSL_REDIRECT=true;
   its cookies become secure and HSTS defaults to one year. Do not enable this
   against an HTTP-only console. Keep the captive Django ingress bound to loopback
   or otherwise inaccessible to untrusted clients. Its route keys are secrets.
3. With the actual service environment loaded, run manage.py check --deploy.
   It rejects the known development secret and nonpositive authentication controls.
4. Run manage.py migrate, then manage.py collectstatic --noinput and restart Django.
   Migration 0015 adds two fingerprint columns. Previously issued captive sessions
   and OTPs have empty fingerprints and are rejected; users must sign in again.
   The renamed management session cookie also requires console users to sign in.
5. Reapply managed captive routes through the console. Validate generated config
   with nginx -t before reloading, using the existing privileged helper.
6. Run the optional TLS integration test:
   python tests/nginx_captive_integration.py /usr/sbin/nginx
   Add --browser with Playwright/Chrome installed for UI/CSP validation.
   Verify desktop/mobile sign-in, reload, wrong code, correct code, logout and 429
   responses on staging. Local Windows NGINX configuration and TLS integration,
   including mobile Chrome sign-in, theme controls, CSRF, cookie stripping and
   logout, passed. Linux deployment validation remains required.
7. Schedule manage.py prune_authentication_limits daily with the service environment
   to remove expired database buckets. It retains at least a day and the longest
   configured limiter window. Authentication/audit retention is a separate policy.

Rollback requires restoring the old application/templates/static assets and
reapplying their generated NGINX routes. The added columns may remain in the database;
do not restore an old database over new production data for a code-only rollback.
Users should sign in again after rollback.

## Boundaries

These are application-layer safeguards, not a claim of protection against every
attack. DDoS filtering, OS and dependency patching, firewall restrictions, privileged
helper permissions, database backups and monitoring still require deployment work.
Existing proxied HTTPS backends currently use proxy_ssl_verify off; trusted backend
certificate verification requires a site-specific CA/name configuration and remains
an outstanding deployment concern. CSP protects Django pages; it does not rewrite
or secure content served by the proxied applications. SMS OTP does not prevent
phishing or SIM swapping; stronger MFA requires a separate authentication design.

References: [OWASP session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html),
[OWASP anti-automation](https://cheatsheetseries.owasp.org/cheatsheets/Bot_Management_and_Anti-Automation_Cheat_Sheet.html),
[Django deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/).

## Local validation (3 October 2026)

Django system checks and migration-drift checks passed. The local database was
migrated successfully. The full Django suite passed (179 tests); JavaScript tests
passed (16 tests). New regression tests cover CAPTCHA reload and invalid-submission
quotas, independent username/IP limits, browser-bound OTPs, agent/IP/idle session
revocation, CSRF rejection, CSP nonces, secure captive cookies, unavailable limiter
storage and deployment configuration rejection. Initial full-suite execution hit
sandbox restrictions on temporary-directory access; the rerun with workspace TEMP
outside the sandbox passed. Existing local NGINX and Chrome tools were used for the
isolated TLS/browser integration; no production server was modified.
