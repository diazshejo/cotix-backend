from django.contrib import admin

from apps.notifications.models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["notification_type", "user", "title", "is_read", "created_at"]
    list_filter  = ["notification_type", "is_read"]
    search_fields = ["user__email", "title", "message"]
    readonly_fields = ["read_at", "created_at", "updated_at"]
