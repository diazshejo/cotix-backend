from rest_framework.exceptions import PermissionDenied
from django.http import Http404

from apps.common.audit import log_security_event


class TenantScopedQuerysetMixin:
    tenant_field = "tenant"

    def get_queryset(self):
        queryset = super().get_queryset().filter(deleted_at__isnull=True)
        user = self.request.user
        if user.is_super_admin:
            return queryset
        if not user.tenant_id:
            raise PermissionDenied("User is not attached to a tenant.")
        if not user.tenant.is_active or user.tenant.status not in {"demo", "active"}:
            raise PermissionDenied("Tenant is inactive.")
        return queryset.filter(**{self.tenant_field: user.tenant})

    def get_object(self):
        try:
            return super().get_object()
        except Http404:
            user = self.request.user
            lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
            lookup_value = self.kwargs.get(lookup_url_kwarg)
            if lookup_value and getattr(user, "is_authenticated", False) and not user.is_super_admin:
                unscoped_qs = super().get_queryset().filter(deleted_at__isnull=True)
                if unscoped_qs.filter(**{self.lookup_field: lookup_value}).exists():
                    log_security_event(
                        "cross_tenant_access_denied",
                        request=self.request,
                        severity="warning",
                        metadata={"model": unscoped_qs.model.__name__, "lookup": str(lookup_value)},
                    )
            raise

    def perform_destroy(self, instance):
        instance.soft_delete()

    def perform_create(self, serializer):
        user = self.request.user
        if user.is_super_admin and serializer.validated_data.get("tenant"):
            serializer.save()
            return
        if not user.tenant_id:
            raise PermissionDenied("User is not attached to a tenant.")
        if not user.tenant.is_active or user.tenant.status not in {"demo", "active"}:
            raise PermissionDenied("Tenant is inactive.")
        serializer.save(tenant=user.tenant)
