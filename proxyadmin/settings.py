from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = [host.strip() for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if host.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "proxies",
]

MIDDLEWARE = [
    "proxies.security.BrowserSecurityHeadersMiddleware",
    "proxies.captive.CaptiveIngressMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "proxies.security.AuthenticationSecurityMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "proxyadmin.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "proxyadmin.wsgi.application"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": os.environ.get("DJANGO_DB_PATH", BASE_DIR / "db.sqlite3")}}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = Path(os.environ.get("DJANGO_MEDIA_ROOT", BASE_DIR / "private_media"))
MEDIA_URL = "/private-media/"
NGINX_ACCESS_LOG_DIR = os.environ.get("NGINX_ACCESS_LOG_DIR", "/var/log/nginx/proxy-admin")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_NAME = "proxy_admin_session"
CSRF_COOKIE_NAME = "proxy_admin_csrf"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
# Enable these in the TLS-terminating deployment.
SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "false").lower() == "true"
SESSION_COOKIE_SECURE = SECURE_SSL_REDIRECT
CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT

# Captive ingress must only be reachable by the local NGINX service.
CAPTIVE_ADMIN_UPSTREAM = os.environ.get("CAPTIVE_ADMIN_UPSTREAM", "http://127.0.0.1:8001")
CAPTIVE_SMS_BACKEND = os.environ.get("CAPTIVE_SMS_BACKEND", "disabled")
CAPTIVE_SMS_API_URL = os.environ.get("CAPTIVE_SMS_API_URL", "")
CAPTIVE_SMS_HTTP_ADAPTER = os.environ.get("CAPTIVE_SMS_HTTP_ADAPTER", "proxies.captive_sms.GatewayAdapter")
CAPTIVE_SMS_MESSAGE_TEMPLATE = os.environ.get("CAPTIVE_SMS_MESSAGE_TEMPLATE", "{#var#} is your OTP to access {domain} in your device - Kolkata Police")
CAPTIVE_OTP_EXPIRY_SECONDS = int(os.environ.get("CAPTIVE_OTP_EXPIRY_SECONDS", "300"))
CAPTIVE_SESSION_SECONDS = int(os.environ.get("CAPTIVE_SESSION_SECONDS", "28800"))
CAPTIVE_RESEND_SECONDS = int(os.environ.get("CAPTIVE_RESEND_SECONDS", "60"))
CAPTIVE_MAX_ATTEMPTS = int(os.environ.get("CAPTIVE_MAX_ATTEMPTS", "5"))
CAPTIVE_DESTINATION_SEND_LIMIT = int(os.environ.get("CAPTIVE_DESTINATION_SEND_LIMIT", "5"))
CAPTIVE_IP_SEND_LIMIT = int(os.environ.get("CAPTIVE_IP_SEND_LIMIT", "20"))
CAPTIVE_USER_SEND_LIMIT = int(os.environ.get("CAPTIVE_USER_SEND_LIMIT", "10"))
CAPTIVE_FQDN_SEND_LIMIT = int(os.environ.get("CAPTIVE_FQDN_SEND_LIMIT", "1000"))
CAPTIVE_VERIFY_IP_LIMIT = int(os.environ.get("CAPTIVE_VERIFY_IP_LIMIT", "100"))

# Layered authentication limits use shared, atomic database counters.
AUTH_CAPTCHA_IP_LIMIT = int(os.environ.get("AUTH_CAPTCHA_IP_LIMIT", "60"))
AUTH_CAPTCHA_SESSION_LIMIT = int(os.environ.get("AUTH_CAPTCHA_SESSION_LIMIT", "30"))
AUTH_POST_IP_LIMIT = int(os.environ.get("AUTH_POST_IP_LIMIT", "30"))
AUTH_POST_SESSION_LIMIT = int(os.environ.get("AUTH_POST_SESSION_LIMIT", "15"))
AUTH_FINGERPRINT_LIMIT = int(os.environ.get("AUTH_FINGERPRINT_LIMIT", "120"))
AUTH_USERNAME_LIMIT = int(os.environ.get("AUTH_USERNAME_LIMIT", "10"))
AUTH_RATE_WINDOW_SECONDS = int(os.environ.get("AUTH_RATE_WINDOW_SECONDS", "300"))
AUTH_SESSION_IDLE_SECONDS = int(os.environ.get("AUTH_SESSION_IDLE_SECONDS", "1800"))
CAPTIVE_BIND_SESSION_IP = os.environ.get("CAPTIVE_BIND_SESSION_IP", "true").lower() == "true"
SESSION_COOKIE_AGE = int(os.environ.get("DJANGO_SESSION_COOKIE_AGE", "28800"))
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 200
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "31536000" if SECURE_SSL_REDIRECT else "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
