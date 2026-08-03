from django.contrib import admin

from apps.integrations.models import IntegrationSyncLog, TenantCatalogIntegration


@admin.register(TenantCatalogIntegration)
class TenantCatalogIntegrationAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "integration_type", "sync_mode", "last_sync_status", "is_active")
    list_filter = ("integration_type", "sync_mode", "last_sync_status", "is_active")
    search_fields = ("name", "tenant__business_name")
    readonly_fields = ("credentials_enc", "custom_headers_enc")


@admin.register(IntegrationSyncLog)
class IntegrationSyncLogAdmin(admin.ModelAdmin):
    list_display = ("integration", "tenant", "status", "started_at", "finished_at", "created_count", "updated_count", "error_count")
    list_filter = ("status", "triggered_by")
    search_fields = ("integration__name", "tenant__business_name")
