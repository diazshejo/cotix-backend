from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.permissions import IsAuthenticated

from apps.common.audit import log_security_event
from apps.common.permissions import TenantSettingsPermission
from apps.tenants.models import Tenant
from apps.tenants.serializers import TenantSerializer


class TenantSettingsView(RetrieveUpdateAPIView):
    serializer_class = TenantSerializer
    permission_classes = [IsAuthenticated, TenantSettingsPermission]

    def get_object(self):
        if self.request.user.is_super_admin:
            return Tenant.objects.filter(deleted_at__isnull=True).first()
        return self.request.user.tenant

    def perform_update(self, serializer):
        tenant = serializer.save()
        log_security_event(
            "tenant_settings_updated",
            request=self.request,
            tenant=tenant,
            metadata={"fields": sorted(serializer.validated_data.keys())},
        )
