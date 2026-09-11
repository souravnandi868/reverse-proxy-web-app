from django.urls import path
from . import views
from . import monitoring

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("proxies/", views.proxy_list, name="proxy_list"),
    path("domains/", views.domains, name="domains"),
    path("servers/", monitoring.servers, name="servers"),
    path("servers/metrics/", monitoring.server_metrics, name="server_metrics"),
    path("monitor/ingest/", monitoring.ingest, name="monitor_ingest"),
    path("proxies/new/", views.proxy_create, name="proxy_create"),
    path("proxies/<int:pk>/edit/", views.proxy_edit, name="proxy_edit"),
    path("proxies/<int:pk>/delete/", views.proxy_delete, name="proxy_delete"),
    path("proxies/<int:pk>/toggle/", views.proxy_toggle, name="proxy_toggle"),
    path("proxies/<int:pk>/apply/", views.proxy_apply, name="proxy_apply"),
    path("proxies/<int:pk>/connectivity/", views.proxy_connectivity, name="proxy_connectivity"),
    path("proxies/<int:pk>/rollback/<int:backup_id>/", views.proxy_rollback, name="proxy_rollback"),
    path("certificates/", views.certificates, name="certificates"),
    path("certificates/<int:pk>/delete/", views.certificate_delete, name="certificate_delete"),
    path("audit/", views.audit, name="audit"),
    path("audit/rows/", views.traffic_rows, name="traffic_rows"),
]
