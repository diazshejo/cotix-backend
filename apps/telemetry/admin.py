from django.contrib import admin

from apps.telemetry.models import AuthEvent, UserSession


@admin.register(AuthEvent)
class AuthEventAdmin(admin.ModelAdmin):
    list_display = ["event_type", "user", "tenant", "ip_address", "browser", "os", "success", "risk_score", "created_at"]
    list_filter = ["event_type", "success", "device_type"]
    search_fields = ["user__email", "ip_address", "tenant__business_name"]
    readonly_fields = [f.name for f in AuthEvent._meta.fields]
    ordering = ["-created_at"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(UserSession)
class UserSessionAdmin(admin.ModelAdmin):
    list_display = ["user", "tenant", "ip_address", "browser", "os", "device_type", "login_at", "last_seen_at", "revoked_at"]
    list_filter = ["device_type"]
    search_fields = ["user__email", "ip_address"]
    readonly_fields = ["id", "user", "tenant", "ip_address", "browser", "os", "device_type", "user_agent",
                       "device_fingerprint", "login_at", "last_seen_at", "metadata"]
    ordering = ["-login_at"]
    date_hierarchy = "login_at"

    def has_add_permission(self, request):
        return False
