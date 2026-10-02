from django.contrib import admin
from django.urls import include, path
from django.contrib.auth import views as auth_views
from .captcha import CaptchaLoginView
from proxies import captive

urlpatterns = [
    path("_captive/login/", captive.login),
    path("_captive/logo/", captive.logo),
    path("_captive/send-otp/", captive.send_otp),
    path("_captive/verify-otp/", captive.verify_otp),
    path("_captive/logout/", captive.logout),
    path("_captive/status/", captive.status),
    path("_captive/check/", captive.check),
    path("_captive/entry/", captive.entry),
    path("admin/", admin.site.urls),
    path("login/", CaptchaLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("proxies.urls")),
]
