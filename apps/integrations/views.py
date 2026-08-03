import time

from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.permissions import TenantRolePermission
from apps.common.viewsets import TenantScopedQuerysetMixin
from apps.integrations.models import IntegrationSyncLog, TenantCatalogIntegration
from apps.integrations.serializers import IntegrationSyncLogSerializer, TenantCatalogIntegrationSerializer
from apps.integrations.services.fetchers import CSVFetcher, RestAPIFetcher
from apps.integrations.services.mappers import (
    apply_filters,
    detected_fields,
    extract_array_from_response,
    preview_records,
    suggest_mapping,
)
from apps.integrations.services.permissions import ensure_tenant_can_use_integrations
from apps.integrations.services.redaction import sanitize_error_message
from apps.integrations.services.sync import CatalogSyncEngine


class TenantCatalogIntegrationViewSet(TenantScopedQuerysetMixin, viewsets.ModelViewSet):
    queryset = TenantCatalogIntegration.objects.select_related("tenant", "created_by")
    serializer_class = TenantCatalogIntegrationSerializer
    permission_classes = [IsAuthenticated, TenantRolePermission]

    def perform_destroy(self, instance):
        instance.soft_delete()

    @action(detail=True, methods=["post"], url_path="test")
    def test_connection(self, request, pk=None):
        integration = self.get_object()
        ensure_tenant_can_use_integrations(integration.tenant)
        start = time.monotonic()
        try:
            records = self._fetch_records_for_preview(integration, request)
            response_time_ms = int((time.monotonic() - start) * 1000)
            sample = records[:3]
            return Response(
                {
                    "status": "success",
                    "products_found": len(records),
                    "sample_products": sample,
                    "detected_fields": detected_fields(sample),
                    "suggested_mapping": suggest_mapping(sample),
                    "response_time_ms": response_time_ms,
                }
            )
        except Exception as exc:
            return Response(
                {
                    "status": "failed",
                    "error_code": getattr(exc, "error_code", "connection_failed"),
                    "error_message": sanitize_error_message(exc),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["get", "post"], url_path="preview")
    def preview(self, request, pk=None):
        integration = self.get_object()
        ensure_tenant_can_use_integrations(integration.tenant)
        records = self._fetch_records_for_preview(integration, request)
        filtered = apply_filters(records, integration.pre_import_filters)
        mapped, errors = preview_records(filtered, integration.field_mapping, limit=10)
        return Response(
            {
                "total_fetched": len(records),
                "total_after_filters": len(filtered),
                "sample_raw": filtered[:5],
                "mapped_preview": mapped,
                "mapping_errors": errors,
                "detected_fields": detected_fields(filtered[:20]),
                "suggested_mapping": suggest_mapping(filtered[:20]),
            }
        )

    @action(detail=True, methods=["post"], url_path="sync")
    def sync(self, request, pk=None):
        integration = self.get_object()
        if not integration.is_active:
            raise ValidationError({"detail": "La integracion esta inactiva."})
        ensure_tenant_can_use_integrations(integration.tenant)
        records = None
        if integration.integration_type == "csv":
            records = self._csv_records(request)
        log = CatalogSyncEngine(integration, triggered_by="manual", triggered_by_user=request.user, records=records).run()
        return Response(IntegrationSyncLogSerializer(log).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="logs")
    def logs(self, request, pk=None):
        integration = self.get_object()
        ensure_tenant_can_use_integrations(integration.tenant)
        logs = integration.sync_logs.filter(tenant=integration.tenant)
        page = self.paginate_queryset(logs)
        serializer = IntegrationSyncLogSerializer(page or logs, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path=r"logs/(?P<log_id>[^/.]+)")
    def log_detail(self, request, pk=None, log_id=None):
        integration = self.get_object()
        ensure_tenant_can_use_integrations(integration.tenant)
        log = get_object_or_404(integration.sync_logs, id=log_id, tenant=integration.tenant)
        return Response(IntegrationSyncLogSerializer(log).data)

    def _fetch_records_for_preview(self, integration, request):
        if integration.integration_type == "csv":
            return self._csv_records(request)
        raw = RestAPIFetcher(integration).fetch_raw()
        return extract_array_from_response(raw, integration.response_array_path)

    def _csv_records(self, request):
        uploaded = request.FILES.get("csv_file")
        if not uploaded:
            raise ValidationError({"csv_file": ["Adjunta un archivo CSV."]})
        return CSVFetcher().fetch_from_upload(uploaded)


class IntegrationSyncLogViewSet(TenantScopedQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = IntegrationSyncLog.objects.select_related("integration", "tenant", "triggered_by_user")
    serializer_class = IntegrationSyncLogSerializer
    permission_classes = [IsAuthenticated, TenantRolePermission]
