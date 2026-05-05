from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from apps.catalog.models import CatalogCategory, CatalogItem
from apps.catalog.serializers import CatalogCategorySerializer, CatalogItemSerializer
from apps.common.permissions import TenantRolePermission
from apps.common.viewsets import TenantScopedQuerysetMixin


class CatalogCategoryViewSet(TenantScopedQuerysetMixin, viewsets.ModelViewSet):
    queryset = CatalogCategory.objects.all()
    serializer_class = CatalogCategorySerializer
    permission_classes = [IsAuthenticated, TenantRolePermission]

    def perform_destroy(self, instance):
        if instance.items.filter(deleted_at__isnull=True, is_active=True).exists():
            raise ValidationError({"detail": "No puedes eliminar una categoria con items activos."})
        instance.soft_delete()


class CatalogItemViewSet(TenantScopedQuerysetMixin, viewsets.ModelViewSet):
    queryset = CatalogItem.objects.select_related("category")
    serializer_class = CatalogItemSerializer
    permission_classes = [IsAuthenticated, TenantRolePermission]
