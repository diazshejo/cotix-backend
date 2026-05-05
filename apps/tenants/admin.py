from django.contrib import admin

from apps.tenants.models import Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("business_name", "slug", "email", "default_currency", "tax_rate", "status", "is_active")
    list_filter = ("status", "is_active", "default_currency", "country_code")
    search_fields = ("business_name", "trade_name", "slug", "email", "nit")
    readonly_fields = ("id", "created_at", "updated_at", "deleted_at")
