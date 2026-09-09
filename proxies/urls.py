from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("proxies/new/", views.proxy_create, name="proxy_create"),
    path("proxies/<int:pk>/edit/", views.proxy_edit, name="proxy_edit"),
    path("proxies/<int:pk>/delete/", views.proxy_delete, name="proxy_delete"),
    path("proxies/<int:pk>/toggle/", views.proxy_toggle, name="proxy_toggle"),
    path("proxies/<int:pk>/apply/", views.proxy_apply, name="proxy_apply"),
    path("proxies/<int:pk>/connectivity/", views.proxy_connectivity, name="proxy_connectivity"),
    path("proxies/<int:pk>/rollback/<int:backup_id>/", views.proxy_rollback, name="proxy_rollback"),
    path("certificates/", views.certificates, name="certificates"),
    path("audit/", views.audit, name="audit"),
]
