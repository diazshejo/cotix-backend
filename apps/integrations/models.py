from django.db import models
from django.db.models import Q

from apps.common.models import BaseModel


class TenantCatalogIntegration(BaseModel):
    class IntegrationType(models.TextChoices):
        REST_API = "rest_api", "REST API"
        CSV = "csv", "CSV"

    class HttpMethod(models.TextChoices):
        GET = "GET", "GET"
        POST = "POST", "POST"

    class AuthType(models.TextChoices):
        NONE = "none", "None"
        API_KEY = "api_key", "API key"
        BEARER = "bearer", "Bearer"
        BASIC = "basic", "Basic"

    class PriceConflictPolicy(models.TextChoices):
        OVERWRITE = "overwrite", "Overwrite"
        SKIP = "skip", "Skip"

    class SyncMode(models.TextChoices):
        MANUAL = "manual", "Manual"
        SCHEDULED = "scheduled", "Scheduled"

    class SyncStatus(models.TextChoices):
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"
        RUNNING = "running", "Running"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="catalog_integrations")
    name = models.CharField(max_length=200)
    integration_type = models.CharField(max_length=30, choices=IntegrationType.choices)
    endpoint_url = models.TextField(blank=True)
    http_method = models.CharField(max_length=10, choices=HttpMethod.choices, default=HttpMethod.GET)
    auth_type = models.CharField(max_length=20, choices=AuthType.choices, default=AuthType.NONE)
    auth_header_name = models.CharField(max_length=100, blank=True)
    credentials_enc = models.TextField(blank=True)
    custom_headers_enc = models.TextField(blank=True)
    request_body_template = models.TextField(blank=True)
    field_mapping = models.JSONField(default=dict, blank=True)
    response_array_path = models.CharField(max_length=200, blank=True)
    pre_import_filters = models.JSONField(default=list, blank=True)
    price_conflict_policy = models.CharField(
        max_length=20,
        choices=PriceConflictPolicy.choices,
        default=PriceConflictPolicy.OVERWRITE,
    )
    sync_mode = models.CharField(max_length=20, choices=SyncMode.choices, default=SyncMode.MANUAL)
    sync_frequency_hours = models.PositiveSmallIntegerField(null=True, blank=True)
    next_sync_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(max_length=20, choices=SyncStatus.choices, blank=True)
    last_sync_created = models.IntegerField(default=0)
    last_sync_updated = models.IntegerField(default=0)
    last_sync_skipped = models.IntegerField(default=0)
    last_sync_errors = models.IntegerField(default=0)
    last_sync_error_detail = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey("users.User", on_delete=models.PROTECT, related_name="created_integrations")

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["tenant", "integration_type"]),
            models.Index(fields=["tenant", "sync_mode", "next_sync_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "name"],
                condition=Q(deleted_at__isnull=True),
                name="uq_catalog_integration_tenant_name_active",
            ),
            models.CheckConstraint(
                condition=Q(integration_type__in=["rest_api", "csv"]),
                name="ck_catalog_integration_type_v1",
            ),
            models.CheckConstraint(condition=Q(http_method__in=["GET", "POST"]), name="ck_catalog_integration_http_method"),
            models.CheckConstraint(
                condition=Q(auth_type__in=["none", "api_key", "bearer", "basic"]),
                name="ck_catalog_integration_auth_type_v1",
            ),
            models.CheckConstraint(
                condition=Q(price_conflict_policy__in=["overwrite", "skip"]),
                name="ck_catalog_integration_price_policy_v1",
            ),
            models.CheckConstraint(
                condition=Q(sync_mode__in=["manual", "scheduled"]),
                name="ck_catalog_integration_sync_mode",
            ),
        ]

    def __str__(self):
        return self.name


class IntegrationSyncLog(BaseModel):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    class TriggeredBy(models.TextChoices):
        MANUAL = "manual", "Manual"
        SCHEDULED = "scheduled", "Scheduled"
        API = "api", "API"

    integration = models.ForeignKey(TenantCatalogIntegration, on_delete=models.CASCADE, related_name="sync_logs")
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="integration_sync_logs")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    total_fetched = models.IntegerField(default=0)
    created_count = models.IntegerField(default=0)
    updated_count = models.IntegerField(default=0)
    skipped_count = models.IntegerField(default=0)
    error_count = models.IntegerField(default=0)
    error_details = models.JSONField(default=list, blank=True)
    triggered_by = models.CharField(max_length=20, choices=TriggeredBy.choices)
    triggered_by_user = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        related_name="triggered_integration_syncs",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["tenant", "started_at"]),
            models.Index(fields=["integration", "started_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.integration_id} {self.status}"
