from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from datetime import timedelta
from django.utils import timezone
from .forms import CertificateBundleForm, ProxyConfigForm
from .models import AuditLog, CertificateBundle, ProxyConfig
from .services import apply_proxy, recent_traffic_logs, rollback_proxy, save_backup, test_backend


def staff_required(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            return HttpResponseForbidden("Staff access required.")
        return view(request, *args, **kwargs)
    return wrapped


def log_action(request, action, target, detail=None):
    AuditLog.objects.create(actor=request.user, action=action, target=target, detail=detail or {}, ip_address=request.META.get("REMOTE_ADDR"))


@staff_required
def dashboard(request):
    from .monitoring import snapshots
    proxies = ProxyConfig.objects.select_related("certificate_bundle").all()
    expiry_cutoff = timezone.localdate() + timedelta(days=30)
    active_certificates = CertificateBundle.objects.filter(is_active=True)
    return render(request, "proxies/dashboard.html", {
        "proxies": proxies,
        "active_count": proxies.filter(enabled=True).count(),
        "certificate_count": active_certificates.count(),
        "certificate_expiring_count": active_certificates.filter(valid_until__isnull=False, valid_until__lte=expiry_cutoff).count(),
        "traffic_logs": recent_traffic_logs(6),
        "reporting_count": sum(server["status"] == "live" for server in snapshots()),
    })


@staff_required
def proxy_list(request):
    return render(request, "proxies/proxy_list.html", {
        "proxies": ProxyConfig.objects.select_related("certificate_bundle").all(),
    })


@staff_required
def domains(request):
    return render(request, "proxies/domains.html", {"proxies": ProxyConfig.objects.all()})


@staff_required
def proxy_create(request):
    form = ProxyConfigForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        proxy = form.save(commit=False)
        proxy.created_by = proxy.updated_by = request.user
        proxy.save()
        log_action(request, "create", proxy.domain_name)
        messages.success(request, "Proxy saved. Apply it after reviewing the generated configuration.")
        return redirect("dashboard")
    return render(request, "proxies/proxy_form.html", {"form": form, "title": "Add proxy"})


@staff_required
def proxy_edit(request, pk):
    proxy = get_object_or_404(ProxyConfig, pk=pk)
    form = ProxyConfigForm(request.POST or None, instance=proxy)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            save_backup(proxy, request.user, "before update")
            proxy = form.save(commit=False)
            proxy.updated_by = request.user
            proxy.save()
        log_action(request, "update", proxy.domain_name)
        messages.success(request, "Proxy updated and previous configuration backed up.")
        return redirect("dashboard")
    return render(request, "proxies/proxy_form.html", {"form": form, "title": f"Edit {proxy.domain_name}", "proxy": proxy})


@staff_required
def proxy_delete(request, pk):
    proxy = get_object_or_404(ProxyConfig, pk=pk)
    if not request.user.is_superuser:
        return HttpResponseForbidden("Only administrators can delete proxy configurations.")
    if request.method == "POST":
        domain = proxy.domain_name
        with transaction.atomic():
            save_backup(proxy, request.user, "before deletion")
            proxy.enabled = False
            proxy.updated_by = request.user
            proxy.save(update_fields=["enabled", "updated_by", "updated_at"])
        result = apply_proxy(proxy)
        if not result.ok:
            messages.error(request, result.message)
            return redirect("dashboard")
        proxy.delete()
        log_action(request, "delete", domain)
        messages.success(request, "Proxy deleted after disabling its managed configuration.")
        return redirect("dashboard")
    return render(request, "proxies/confirm_delete.html", {"proxy": proxy})


@staff_required
def proxy_toggle(request, pk):
    proxy = get_object_or_404(ProxyConfig, pk=pk)
    if request.method == "POST":
        previous_enabled = proxy.enabled
        with transaction.atomic():
            save_backup(proxy, request.user, "before status change")
            proxy.enabled = not proxy.enabled
            proxy.updated_by = request.user
            proxy.save(update_fields=["enabled", "updated_by", "updated_at"])
        action = "enable" if proxy.enabled else "disable"
        result = apply_proxy(proxy)
        if not result.ok:
            proxy.enabled = previous_enabled
            proxy.save(update_fields=["enabled", "updated_at"])
            messages.error(request, f"{proxy.domain_name}: {result.message}")
            return redirect("dashboard")
        proxy.last_applied_at = timezone.now()
        proxy.save(update_fields=["last_applied_at"])
        log_action(request, action, proxy.domain_name)
        messages.success(request, f"{proxy.domain_name} {action}d and NGINX was reloaded.")
    return redirect("dashboard")


@staff_required
def proxy_apply(request, pk):
    proxy = get_object_or_404(ProxyConfig, pk=pk)
    if request.method == "POST":
        result = apply_proxy(proxy)
        if result.ok:
            proxy.last_applied_at = timezone.now()
            proxy.save(update_fields=["last_applied_at"])
            log_action(request, "apply", proxy.domain_name, {"result": result.message})
            messages.success(request, f"{proxy.domain_name}: {result.message}")
        else:
            messages.error(request, f"{proxy.domain_name}: {result.message}")
    return redirect("dashboard")


@staff_required
def proxy_connectivity(request, pk):
    proxy = get_object_or_404(ProxyConfig, pk=pk)
    result = test_backend(proxy)
    log_action(request, "connectivity", proxy.domain_name, {"ok": result.ok, "message": result.message})
    messages.success(request, f"{proxy.domain_name}: {result.message}") if result.ok else messages.error(request, f"{proxy.domain_name}: {result.message}")
    return redirect("dashboard")


@staff_required
def proxy_rollback(request, pk, backup_id):
    proxy = get_object_or_404(ProxyConfig, pk=pk)
    backup = get_object_or_404(proxy.backups, pk=backup_id)
    if request.method == "POST":
        result = rollback_proxy(proxy, backup)
        if result.ok:
            log_action(request, "rollback", proxy.domain_name, {"version": backup.version})
            messages.success(request, f"Rolled back to backup v{backup.version}.")
        else:
            messages.error(request, result.message)
    return redirect("dashboard")


@staff_required
def certificates(request):
    form = CertificateBundleForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        bundle = form.save(commit=False)
        bundle.uploaded_by = request.user
        bundle.save()
        log_action(request, "certificate", bundle.name)
        messages.success(request, "Certificate bundle uploaded securely.")
        return redirect("certificates")
    return render(request, "proxies/certificates.html", {"form": form, "certificates": CertificateBundle.objects.select_related("uploaded_by")})


@staff_required
def audit(request):
    return render(request, "proxies/audit.html", {"traffic_logs": recent_traffic_logs()})
