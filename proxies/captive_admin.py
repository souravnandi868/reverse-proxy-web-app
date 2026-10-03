from functools import wraps
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from .views import staff_required
from .models import ProxyConfig
from .captive_models import CaptivePortalUser, CaptiveAudit


def permission(name):
    def decorator(view):
        @staff_required
        @never_cache
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.has_perm("proxies." + name):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


class AuthorizedUserForm(forms.ModelForm):
    class Meta:
        model = CaptivePortalUser
        fields = ["name", "section", "rank", "mobile_number", "email_address", "is_enabled", "all_fqdns", "proxies"]

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["proxies"].widget = forms.CheckboxSelectMultiple()
        self.fields["proxies"].queryset = ProxyConfig.objects.filter(captive_portal_enabled=True)
        self.fields["proxies"].label = "Selected FQDNs"
        self.fields["proxies"].help_text = "Check FQDNs to add access; uncheck them to remove access. Selected FQDNs apply when access to all captive-enabled FQDNs is off."
        if actor is not None:
            for field, perm in [("is_enabled", "toggle"), ("all_fqdns", "assign"), ("proxies", "assign")]:
                self.fields[field].disabled = not actor.has_perm(f"proxies.{perm}_captiveportaluser")
            if not self.instance.pk and self.fields["is_enabled"].disabled:
                self.initial["is_enabled"] = False
            if self.fields["proxies"].disabled:
                self.fields["proxies"].help_text = "FQDN assignment permission is required to change access."


def audit(actor, action, user):
    CaptiveAudit.objects.create(actor=actor, action=action, user=user)


def save_user(form, actor):
    user = form.save(commit=False)
    old = CaptivePortalUser.objects.filter(pk=user.pk).first()
    old_proxies = set(old.proxies.values_list("pk", flat=True)) if old else set()
    user.updated_by = actor
    if not old:
        user.created_by = actor
    user.save()
    audit(actor, "user_edited" if old else "user_registered", user)
    if old:
        if old.is_enabled != user.is_enabled:
            audit(actor, "user_enabled" if user.is_enabled else "user_disabled", user)
        if (old.mobile_number, old.email_address) != (user.mobile_number, user.email_address):
            audit(actor, "contact_changed", user)
        if old.all_fqdns != user.all_fqdns:
            audit(actor, "all_fqdns_changed", user)
    form.save_m2m()
    if old and old_proxies != set(user.proxies.values_list("pk", flat=True)):
        audit(actor, "fqdn_access_changed", user)
    return user


@permission("view_captiveportaluser")
@require_http_methods(["GET"])
def users_export_excel(request):
    from .authorized_users_excel import build_authorized_users_excel

    rows = CaptivePortalUser.objects.filter(deleted_at__isnull=True).select_related(
        "created_by", "updated_by").prefetch_related("proxies")
    response = HttpResponse(build_authorized_users_excel(rows),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="authorized-users.xlsx"'
    return response


@permission("view_captiveportaluser")
def users(request):
    rows = CaptivePortalUser.objects.filter(deleted_at__isnull=True).prefetch_related("proxies")
    query = request.GET.get("q", "")
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(mobile_number__icontains=query) | Q(email_address__icontains=query))
    for field in ("section", "rank"):
        if request.GET.get(field):
            rows = rows.filter(**{field + "__icontains": request.GET[field]})
    if request.GET.get("status") in {"enabled", "disabled"}:
        rows = rows.filter(is_enabled=request.GET["status"] == "enabled")
    if request.GET.get("fqdn", "").isdigit():
        rows = rows.filter(Q(proxies__pk=request.GET["fqdn"]) | Q(all_fqdns=True)).distinct()
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "captive/users.html", {"page": Paginator(rows, 25).get_page(request.GET.get("page")),
        "filters": params.urlencode(), "proxies": ProxyConfig.objects.filter(captive_portal_enabled=True)})


@require_http_methods(["GET", "POST"])
def user_edit(request, pk=None):
    @permission(("change" if pk else "add") + "_captiveportaluser")
    def inner(request):
        instance = get_object_or_404(CaptivePortalUser, pk=pk, deleted_at__isnull=True) if pk else None
        form = AuthorizedUserForm(request.POST if request.method == "POST" else None, instance=instance, actor=request.user)
        if request.method == "POST" and form.is_valid():
            with transaction.atomic():
                save_user(form, request.user)
            messages.success(request, "Authorized user saved.")
            return redirect("captive_users")
        return render(request, "captive/user_form.html", {"form": form, "title": "Edit authorized user" if pk else "Register authorized user"})
    return inner(request)


@require_http_methods(["GET", "POST"])
def user_action(request, pk, action):
    perm = {"toggle": "toggle", "delete": "delete", "assign": "assign"}.get(action)
    if perm is None:
        raise PermissionDenied

    @permission(perm + "_captiveportaluser")
    def inner(request):
        user = get_object_or_404(CaptivePortalUser, pk=pk, deleted_at__isnull=True)
        form = None
        if action == "assign":
            class AssignmentForm(forms.ModelForm):
                class Meta:
                    model = CaptivePortalUser
                    fields = ["all_fqdns", "proxies"]
            form = AssignmentForm(request.POST if request.method == "POST" else None, instance=user)
            form.fields["proxies"].queryset = ProxyConfig.objects.filter(captive_portal_enabled=True)
        if request.method == "POST" and (form is None or form.is_valid()):
            if action == "delete" and request.POST.get("confirm") != str(user.pk):
                raise PermissionDenied
            with transaction.atomic():
                user.updated_by = request.user
                if action == "delete":
                    audit(request.user, "user_deleted", user)
                    user.delete()
                elif action == "toggle":
                    user.is_enabled = not user.is_enabled
                    user.save()
                    audit(request.user, "user_enabled" if user.is_enabled else "user_disabled", user)
                else:
                    save_user(form, request.user)
                    audit(request.user, "fqdn_access_changed", user)
            return redirect("captive_users")
        return render(request, "captive/user_action.html", {"captive_user": user, "action": action, "form": form})
    return inner(request)


def filtered_audit_rows(request):
    rows = CaptiveAudit.objects.select_related("user", "proxy", "actor")
    for field in ("action", "channel"):
        if request.GET.get(field):
            rows = rows.filter(**{field: request.GET[field]})
    for field, lookup in [("administrator", "actor__username__icontains"), ("user", "user__name__icontains"), ("fqdn", "proxy__domain_name__icontains")]:
        if request.GET.get(field):
            rows = rows.filter(**{lookup: request.GET[field]})
    dates = {}
    for field, lookup in [("start", "created_at__gte"), ("end", "created_at__lte")]:
        value = request.GET.get(field, "")
        if not value:
            continue
        try:
            date = parse_datetime(value)
        except ValueError:
            date = None
        if date is None:
            raise ValueError(f"Enter a valid {field} date and time.")
        from django.utils.timezone import is_naive, make_aware
        dates[field] = make_aware(date) if is_naive(date) else date
        rows = rows.filter(**{lookup: dates[field]})
    if "start" in dates and "end" in dates and dates["start"] > dates["end"]:
        raise ValueError("Start date and time must be before or equal to end date and time.")
    return rows


@permission("view_captiveaudit")
def audit_page(request):
    from django.utils.timezone import get_current_timezone_name
    error = ""
    try:
        rows = filtered_audit_rows(request)
    except ValueError as exc:
        error = str(exc)
        rows = CaptiveAudit.objects.none()
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "captive/audit.html", {"page": Paginator(rows, 50).get_page(request.GET.get("page")),
        "filters": params.urlencode(), "error": error, "audit_timezone": get_current_timezone_name()}, status=400 if error else 200)


@permission("view_captiveaudit")
@require_http_methods(["GET"])
def audit_export_excel(request):
    from .captive_excel import build_captive_audit_excel
    try:
        rows = filtered_audit_rows(request)
    except ValueError as exc:
        return HttpResponse(str(exc), status=400, content_type="text/plain")
    response = HttpResponse(build_captive_audit_excel(rows.iterator(chunk_size=2000)),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="captive-audit.xlsx"'
    return response
