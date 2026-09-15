from django.contrib import admin
from django.urls import include, path
from django.contrib.auth import views as auth_views
from .captcha import CaptchaLoginView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", CaptchaLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("proxies.urls")),
]
