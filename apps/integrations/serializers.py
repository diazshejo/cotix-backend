from django.utils import timezone
from rest_framework import serializers

from apps.common.validators import clean_text
from apps.integrations.models import IntegrationSyncLog, TenantCatalogIntegration
from apps.integrations.services.crypto import encrypt_json, encrypt_text
from apps.integrations.services.permissions import ensure_tenant_can_use_integrations
from apps.integrations.services.validators import validate_external_url


class TenantCatalogIntegrationSerializer(serializers.ModelSerializer):
    credentials = serializers.JSONField(write_only=True, required=False, allow_null=True)
    custom_headers = serializers.JSONField(write_only=True, required=False, allow_null=True)
    has_credentials = serializers.SerializerMethodField()

    class Meta:
        model = TenantCatalogIntegration
        fields = [
            "id",
            "name",
            "integration_type",
            "endpoint_url",
            "http_method",
            "auth_type",
            "auth_header_name",
            "credentials",
            "custom_headers",
            "has_credentials",
            "request_body_template",
            "field_mapping",
            "response_array_path",
            "pre_import_filters",
            "price_conflict_policy",
            "sync_mode",
            "sync_frequency_hours",
            "next_sync_at",
            "last_synced_at",
            "last_sync_status",
            "last_sync_created",
            "last_sync_updated",
            "last_sync_skipped",
            "last_sync_errors",
            "last_sync_error_detail",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "has_credentials",
            "next_sync_at",
            "last_synced_at",
            "last_sync_status",
            "last_sync_created",
            "last_sync_updated",
            "last_sync_skipped",
            "last_sync_errors",
            "last_sync_error_detail",
            "created_at",
            "updated_at",
        ]

    def get_has_credentials(self, obj):
        return bool(obj.credentials_enc)

    def validate_name(self, value):
        value = clean_text(value)
        if not value:
            raise serializers.ValidationError("El nombre de la integracion es obligatorio.")
        return value

    def validate_integration_type(self, value):
        if value not in {"rest_api", "csv"}:
            raise serializers.ValidationError("Solo rest_api y csv estan soportados en V1.")
        return value

    def validate_endpoint_url(self, value):
        integration_type = self.initial_data.get("integration_type") or getattr(self.instance, "integration_type", "")
        if integration_type == "rest_api":
            return validate_external_url(value)
        return value or ""

    def validate_field_mapping(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("El mapping debe ser un objeto.")
        return value

    def validate_pre_import_filters(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Los filtros deben ser una lista.")
        allowed = {"eq", "neq", "contains", "gt", "gte", "lt", "lte"}
        for item in value:
            if not isinstance(item, dict) or item.get("operator", "eq") not in allowed:
                raise serializers.ValidationError("Filtro invalido.")
        return value

    def validate(self, attrs):
        integration_type = attrs.get("integration_type", getattr(self.instance, "integration_type", ""))
        if integration_type == "rest_api" and not attrs.get("endpoint_url", getattr(self.instance, "endpoint_url", "")):
            raise serializers.ValidationError({"endpoint_url": ["La URL es obligatoria para REST API."]})
        sync_mode = attrs.get("sync_mode", getattr(self.instance, "sync_mode", "manual"))
        frequency = attrs.get("sync_frequency_hours", getattr(self.instance, "sync_frequency_hours", None))
        if sync_mode == "scheduled" and not frequency:
            raise serializers.ValidationError({"sync_frequency_hours": ["La frecuencia es obligatoria para sync programada."]})
        if frequency is not None and frequency <= 0:
            raise serializers.ValidationError({"sync_frequency_hours": ["La frecuencia debe ser mayor a cero."]})
        tenant = getattr(self.context["request"].user, "tenant", None)
        deactivating = self.instance is not None and self.instance.is_active and attrs.get("is_active") is False
        if self.instance is None:
            ensure_tenant_can_use_integrations(tenant)
        elif self.instance.is_active and not deactivating:
            ensure_tenant_can_use_integrations(tenant)
        elif attrs.get("is_active") is True and not self.instance.is_active:
            ensure_tenant_can_use_integrations(tenant)
        name = attrs.get("name", getattr(self.instance, "name", ""))
        if tenant and name:
            qs = TenantCatalogIntegration.objects.filter(tenant=tenant, name__iexact=name, deleted_at__isnull=True)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({"name": ["Ya existe una integracion con este nombre."]})
        return attrs

    def create(self, validated_data):
        credentials = validated_data.pop("credentials", None)
        custom_headers = validated_data.pop("custom_headers", None)
        self._set_encrypted(validated_data, credentials, custom_headers)
        user = self.context["request"].user
        if validated_data.get("sync_mode") == "scheduled" and validated_data.get("sync_frequency_hours"):
            validated_data["next_sync_at"] = timezone.now()
        return super().create({**validated_data, "created_by": user})

    def update(self, instance, validated_data):
        credentials = validated_data.pop("credentials", serializers.empty)
        custom_headers = validated_data.pop("custom_headers", serializers.empty)
        next_auth_type = validated_data.get("auth_type", instance.auth_type)
        if credentials is not serializers.empty:
            instance.credentials_enc = _encrypt_credentials(next_auth_type, credentials)
        if custom_headers is not serializers.empty:
            instance.custom_headers_enc = encrypt_json(custom_headers or {})
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if instance.sync_mode == "scheduled" and instance.sync_frequency_hours and not instance.next_sync_at:
            instance.next_sync_at = timezone.now()
        if instance.sync_mode == "manual":
            instance.next_sync_at = None
        instance.save()
        return instance

    def _set_encrypted(self, attrs, credentials, custom_headers):
        auth_type = attrs.get("auth_type", "none")
        attrs["credentials_enc"] = _encrypt_credentials(auth_type, credentials)
        attrs["custom_headers_enc"] = encrypt_json(custom_headers or {})


def _encrypt_credentials(auth_type, credentials):
    if auth_type == "none" or credentials in (None, "", {}):
        return ""
    if auth_type == "basic" and isinstance(credentials, dict):
        username = credentials.get("username", "")
        password = credentials.get("password", "")
        return encrypt_text(f"{username}:{password}")
    if isinstance(credentials, dict):
        token = credentials.get("value") or credentials.get("token") or credentials.get("api_key") or ""
        return encrypt_text(token)
    return encrypt_text(str(credentials))


class IntegrationSyncLogSerializer(serializers.ModelSerializer):
    results = serializers.SerializerMethodField()

    class Meta:
        model = IntegrationSyncLog
        fields = [
            "id",
            "integration",
            "status",
            "started_at",
            "finished_at",
            "duration_seconds",
            "total_fetched",
            "created_count",
            "updated_count",
            "skipped_count",
            "error_count",
            "error_details",
            "triggered_by",
            "triggered_by_user",
            "results",
        ]
        read_only_fields = fields

    def get_results(self, obj):
        return {
            "total_fetched": obj.total_fetched,
            "created": obj.created_count,
            "updated": obj.updated_count,
            "skipped": obj.skipped_count,
            "errors": obj.error_count,
        }


class IntegrationPreviewSerializer(serializers.Serializer):
    csv_file = serializers.FileField(required=False)


class IntegrationTestResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    products_found = serializers.IntegerField(required=False)
    sample_products = serializers.ListField(required=False)
    detected_fields = serializers.ListField(required=False)
    suggested_mapping = serializers.DictField(required=False)
    error_code = serializers.CharField(required=False)
    error_message = serializers.CharField(required=False)
