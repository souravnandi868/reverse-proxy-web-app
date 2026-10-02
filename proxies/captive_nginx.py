import re
from django.conf import settings
from .captive import route_key


def preamble(proxy):
    # Strip portal/management cookies while preserving application cookies.
    suffix = route_key(proxy.domain_name)[:12]
    variable = "captive_cookie_" + suffix
    captive_only = "captive_only_" + suffix
    before, after = "cp_before_" + suffix, "cp_after_" + suffix
    config = f'''limit_req_zone $binary_remote_addr zone=captive_auth_{suffix}:1m rate=2r/s;
limit_conn_zone $binary_remote_addr zone=captive_conn_{suffix}:1m;
map $http_cookie ${captive_only} {{
    default $http_cookie;
    "~^(?<{before}>.*?)(?:^|;\\s*)__Host-captive_session=[^;]*(?<{after}>.*)$" "${before}${after}";
}}
log_format captive_{suffix} escape=json '{{"time":"$time_iso8601","source_ip":"$remote_addr","destination_fqdn":"$host","destination_server":"$upstream_addr","request":"$request_method $uri $server_protocol","status":$status,"bytes":$body_bytes_sent,"received_bytes":$request_length,"sent_bytes":$bytes_sent,"request_time":$request_time}}';
'''
    incoming = captive_only
    for index, cookie in enumerate((settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME)):
        output = f"console_cookie_{suffix}_{index}"
        before, after = f"cookie_before_{suffix}_{index}", f"cookie_after_{suffix}_{index}"
        escaped_cookie = re.escape(cookie)
        config += f'''map ${incoming} ${output} {{
    default ${incoming};
    "~^(?<{before}>.*?)(?:^|;\\s*){escaped_cookie}=[^;]*(?<{after}>.*)$" "${before}${after}";
}}
'''
        incoming = output
    private_names = "|".join(re.escape(name) for name in
                             ("__Host-captive_session", settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME))
    # Duplicate private cookie names must not leak a second copy upstream.
    config += f'''map ${incoming} ${variable} {{
    default ${incoming};
    "~(?:^|;\\s*)(?:{private_names})=" "";
}}
'''
    return config, variable, "captive_" + suffix


def locations(proxy):
    if proxy.incoming_protocol != "https" or not proxy.certificate_bundle:
        raise ValueError("Captive portal routes require HTTPS and a certificate.")
    upstream = settings.CAPTIVE_ADMIN_UPSTREAM
    if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", upstream):
        raise ValueError("CAPTIVE_ADMIN_UPSTREAM must be a loopback HTTP URL with a port.")
    common = f'''        proxy_pass {upstream};
        proxy_set_header Host {proxy.domain_name};
        proxy_set_header X-Captive-Key {route_key(proxy.domain_name)};
        proxy_set_header X-Captive-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For "";
        proxy_set_header Authorization "";
        proxy_set_header X-Original-URI $request_uri;
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
        proxy_cache off;
        proxy_no_cache 1;
        access_log off;
        add_header Cache-Control "no-store" always;
'''
    result = ""
    for endpoint in ("login", "send-otp", "verify-otp", "logout", "status"):
        result += f'''    location = /_captive/{endpoint}/ {{
        auth_request off;
        client_max_body_size 8k;
        limit_req zone=captive_auth_{route_key(proxy.domain_name)[:12]} burst=10 nodelay;
        limit_req_status 429;
        limit_conn captive_conn_{route_key(proxy.domain_name)[:12]} 20;
        limit_conn_status 429;
        proxy_set_header X-Captive-Internal "";
{common}    }}
'''
    result += f'''    location = /_captive_auth {{
        internal;
        rewrite ^ /_captive/check/ break;
        proxy_method GET;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header X-Captive-Internal 1;
{common}    }}
    location @captive_login {{
        rewrite ^ /_captive/entry/ break;
        proxy_method GET;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header X-Captive-Internal 1;
{common}    }}
    location ^~ /_captive/ {{ return 404; }}
'''
    return result
