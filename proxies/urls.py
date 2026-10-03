from django.urls import path
from . import views
from . import monitoring, account, usage
from . import captive_admin

urlpatterns = [
    path("authorized-users/", captive_admin.users, name="captive_users"),
    path("authorized-users/export.xlsx", captive_admin.users_export_excel, name="captive_users_export_excel"),
    path("authorized-users/new/", captive_admin.user_edit, name="captive_user_add"),
    path("authorized-users/<int:pk>/edit/", captive_admin.user_edit, name="captive_user_edit"),
    path("authorized-users/<int:pk>/<str:action>/", captive_admin.user_action, name="captive_user_action"),
    path("captive-audit/", captive_admin.audit_page, name="captive_audit"),
    path("captive-audit/export.xlsx", captive_admin.audit_export_excel, name="captive_audit_export_excel"),
    path("usage/", usage.page, name="website_usage"),
    path("usage/metrics/", usage.metrics, name="website_usage_metrics"),
    path("account/", account.information, name="account_information"),
    path("account/contact/", account.contact, name="account_contact"),
    path("account/password/", account.password, name="account_password"),
    path("", views.dashboard, name="dashboard"),
    path("proxies/", views.proxy_list, name="proxy_list"),
    path("domains/", views.domains, name="domains"),
    path("servers/", monitoring.servers, name="servers"),
    path("servers/metrics/", monitoring.server_metrics, name="server_metrics"),
    path("monitor/ingest/", monitoring.ingest, name="monitor_ingest"),
    path("proxies/new/", views.proxy_create, name="proxy_create"),
    path("proxies/<int:pk>/edit/", views.proxy_edit, name="proxy_edit"),
    path("proxies/export.pdf", views.proxy_export_pdf, name="proxy_export_pdf"),
    path("proxies/<int:pk>/delete/", views.proxy_delete, name="proxy_delete"),
    path("proxies/<int:pk>/toggle/", views.proxy_toggle, name="proxy_toggle"),
    path("proxies/<int:pk>/apply/", views.proxy_apply, name="proxy_apply"),
    path("proxies/<int:pk>/connectivity/", views.proxy_connectivity, name="proxy_connectivity"),
    path("proxies/<int:pk>/rollback/<int:backup_id>/", views.proxy_rollback, name="proxy_rollback"),
    path("certificates/", views.certificates, name="certificates"),
    path("certificates/<int:pk>/delete/", views.certificate_delete, name="certificate_delete"),
    path("audit/", views.audit, name="audit"),
    path("audit/rows/", views.traffic_rows, name="traffic_rows"),
    path("audit/export.xlsx", views.traffic_export, name="traffic_export"),
]
