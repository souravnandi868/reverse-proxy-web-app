from django.contrib import admin
from .models import AuditLog, CertificateBundle, ConfigurationBackup, ProxyConfig
from .forms import ProxyConfigForm


@admin.register(ProxyConfig)
class ProxyConfigAdmin(admin.ModelAdmin):
    form = ProxyConfigForm
    readonly_fields = ("public_ip",)

    def has_delete_permission(self, request, obj=None):
        # All proxy deletion must pass through the helper-backed confirmation view.
        return False


admin.site.register([CertificateBundle, ConfigurationBackup, AuditLog])

from django.shortcuts import redirect
from .captive_models import CaptivePortalUser, CaptiveAudit


@admin.register(CaptivePortalUser)
class CaptivePortalUserAdmin(admin.ModelAdmin):
    list_display = ("name", "section", "rank", "mobile_number", "email_address", "is_enabled", "all_fqdns")
    search_fields = ("name", "mobile_number", "email_address")
    list_filter = ("is_enabled", "section", "rank", "proxies")
    actions = None

    def get_queryset(self, request):
        return super().get_queryset(request).filter(deleted_at__isnull=True)

    def add_view(self, request, form_url="", extra_context=None):
        return redirect("captive_user_add")

    def change_view(self, request, object_id, form_url="", extra_context=None):
        return redirect("captive_user_edit", pk=object_id)

    def delete_view(self, request, object_id, extra_context=None):
        return redirect("captive_user_action", pk=object_id, action="delete")


@admin.register(CaptiveAudit)
class CaptiveAuditAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor", "user", "proxy", "channel")
    list_filter = ("action", "channel", "created_at")
    search_fields = ("actor__username", "user__name", "proxy__domain_name")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
