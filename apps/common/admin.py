from django.contrib import admin

from apps.common.models import SecurityEvent


@admin.register(SecurityEvent)
class SecurityEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "severity", "tenant", "user", "ip_address", "method", "created_at")
    list_filter = ("severity", "event_type", "method", "created_at")
    search_fields = ("event_type", "tenant__business_name", "tenant__slug", "user__email", "ip_address", "path")
    readonly_fields = (
        "id",
        "tenant",
        "user",
        "event_type",
        "severity",
        "ip_address",
        "user_agent",
        "path",
        "method",
        "metadata",
        "created_at",
        "updated_at",
        "deleted_at",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
