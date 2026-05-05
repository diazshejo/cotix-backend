from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from apps.clients.models import Client
from apps.clients.serializers import ClientSerializer
from apps.common.permissions import TenantRolePermission
from apps.common.viewsets import TenantScopedQuerysetMixin


class ClientViewSet(TenantScopedQuerysetMixin, viewsets.ModelViewSet):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    permission_classes = [IsAuthenticated, TenantRolePermission]
    search_fields = ["name", "company_name", "email", "nit"]

    def perform_destroy(self, instance):
        if instance.quotes.filter(deleted_at__isnull=True).exclude(status__in=["expired", "rejected"]).exists():
            raise ValidationError({"detail": "No puedes eliminar un cliente con cotizaciones activas."})
        instance.soft_delete()
