import re
from django.conf import settings
from .captive import route_key


def preamble(proxy):
    # Per-route map strips only our session cookie; backend cookies are preserved.
    suffix = route_key(proxy.domain_name)[:12]
    variable = "captive_cookie_" + suffix
    before, after = "cp_before_" + suffix, "cp_after_" + suffix
    config = f'''map $http_cookie ${variable} {{
    default $http_cookie;
    "~^(?<{before}>.*?)(?:^|;\\s*)__Host-captive_session=[^;]*(?<{after}>.*)$" "${before}${after}";
}}
log_format captive_{suffix} escape=json '{{"time":"$time_iso8601","source_ip":"$remote_addr","destination_fqdn":"$host","destination_server":"$upstream_addr","request":"$request_method $uri $server_protocol","status":$status,"bytes":$body_bytes_sent,"received_bytes":$request_length,"sent_bytes":$bytes_sent,"request_time":$request_time}}';
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
