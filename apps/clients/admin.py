from django.contrib import admin

from apps.clients.models import Client


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "company_name", "tenant", "email", "nit", "is_active", "created_at")
    list_filter = ("is_active", "tenant", "country_code")
    search_fields = ("name", "company_name", "email", "nit", "tenant__business_name", "tenant__slug")
    readonly_fields = ("id", "created_at", "updated_at", "deleted_at")
