from rest_framework.permissions import BasePermission, SAFE_METHODS

from apps.common.audit import log_security_event
from apps.tenants.services.plans import PlanService


def tenant_is_operational(user):
    if user.is_super_admin:
        return True
    return bool(user.tenant_id and user.tenant.is_active and user.tenant.status in {"demo", "active"})


def deny(request, event_type="permission_denied", metadata=None):
    log_security_event(event_type, request=request, severity="warning", metadata=metadata or {})
    return False


def require_current_trial(request):
    if PlanService.trial_expired(request.user.tenant):
        log_security_event("trial_expired_write_denied", request=request, severity="warning")
        PlanService.require_active_trial(request.user.tenant)


class IsTenantMember(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated or not request.user.is_active:
            return False
        if request.user.is_super_admin:
            return True
        if not tenant_is_operational(request.user):
            return deny(request, metadata={"reason": "inactive_tenant_or_missing_tenant"})
        if request.method not in SAFE_METHODS:
            require_current_trial(request)
        return True


class IsTenantAdminOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated or not request.user.is_active:
            return False
        if request.user.is_super_admin:
            return True
        if not tenant_is_operational(request.user):
            return deny(request, metadata={"reason": "inactive_tenant_or_missing_tenant"})
        if request.method in SAFE_METHODS:
            return True
        if request.user.role != "tenant_admin":
            return deny(request, metadata={"required_role": "tenant_admin", "role": request.user.role})
        require_current_trial(request)
        return True


class TenantRolePermission(BasePermission):
    write_roles = {"tenant_admin", "seller"}

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated or not request.user.is_active:
            return False
        if request.user.is_super_admin:
            return True
        if not tenant_is_operational(request.user):
            return deny(request, metadata={"reason": "inactive_tenant_or_missing_tenant"})
        if request.method in SAFE_METHODS:
            return request.user.role in {"tenant_admin", "seller", "viewer"}
        if request.user.role not in self.write_roles:
            return deny(request, metadata={"required_roles": sorted(self.write_roles), "role": request.user.role})
        require_current_trial(request)
        return True


class TenantUserAdminPermission(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated or not request.user.is_active:
            return False
        if request.user.is_super_admin:
            return True
        if not tenant_is_operational(request.user):
            return deny(request, metadata={"reason": "inactive_tenant_or_missing_tenant"})
        if request.user.role != "tenant_admin":
            return deny(request, metadata={"required_role": "tenant_admin", "role": request.user.role})
        if request.method not in SAFE_METHODS:
            require_current_trial(request)
        return True


class TenantBillingPermission(BasePermission):
    """Allow administrators to subscribe even after their free trial ends."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated or not request.user.is_active:
            return False
        if request.user.is_super_admin:
            return True
        if not tenant_is_operational(request.user):
            return deny(request, metadata={"reason": "inactive_tenant_or_missing_tenant"})
        if request.user.role != "tenant_admin":
            return deny(request, metadata={"required_role": "tenant_admin", "role": request.user.role})
        return True


class TenantSettingsPermission(BasePermission):
    critical_fields = {
        "nit",
        "tax_regime",
        "default_currency",
        "tax_label",
        "tax_rate",
        "prices_include_tax",
        "quote_prefix",
    }

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated or not request.user.is_active:
            return False
        if request.user.is_super_admin:
            return True
        if not tenant_is_operational(request.user):
            return deny(request, metadata={"reason": "inactive_tenant_or_missing_tenant"})
        if request.method in SAFE_METHODS:
            return request.user.role in {"tenant_admin", "seller", "viewer"}
        if request.user.role != "tenant_admin":
            changed_critical = sorted(self.critical_fields.intersection(set(request.data.keys())))
            return deny(
                request,
                metadata={
                    "required_role": "tenant_admin",
                    "role": request.user.role,
                    "critical_fields": changed_critical,
                },
            )
        require_current_trial(request)
        return True
