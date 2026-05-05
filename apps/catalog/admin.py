from django.contrib import admin

from apps.catalog.models import CatalogCategory, CatalogItem


@admin.register(CatalogCategory)
class CatalogCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "is_active", "created_at")
    list_filter = ("is_active", "tenant")
    search_fields = ("name", "tenant__business_name", "tenant__slug")
    readonly_fields = ("id", "created_at", "updated_at", "deleted_at")


@admin.register(CatalogItem)
class CatalogItemAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "category", "item_type", "sku", "unit_price", "currency", "is_active")
    list_filter = ("item_type", "currency", "taxable", "is_active", "tenant")
    search_fields = ("name", "sku", "description", "tenant__business_name", "tenant__slug")
    readonly_fields = ("id", "created_at", "updated_at", "deleted_at")
