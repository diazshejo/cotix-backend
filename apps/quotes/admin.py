from django.contrib import admin

from apps.quotes.models import Quote, QuoteAcceptance, QuoteActivityLog, QuoteItem


class QuoteItemInline(admin.TabularInline):
    model = QuoteItem
    extra = 0
    fields = ("position", "description", "quantity", "unit", "unit_price", "discount_type", "discount_value", "taxable")
    readonly_fields = ("id", "created_at", "updated_at", "deleted_at")


@admin.register(Quote)
class QuoteAdmin(admin.ModelAdmin):
    list_display = ("quote_number", "tenant", "client", "status", "currency", "total", "issue_date", "expiry_date")
    list_filter = ("status", "currency", "tenant", "issue_date", "expiry_date")
    search_fields = ("quote_number", "title", "client__name", "client__email", "tenant__business_name", "tenant__slug")
    readonly_fields = (
        "id",
        "public_token",
        "subtotal",
        "discount_total",
        "taxable_base",
        "total_tax",
        "total",
        "pdf_generated_at",
        "client_first_viewed_at",
        "client_responded_at",
        "change_request_message",
        "revision_of",
        "created_at",
        "updated_at",
        "deleted_at",
    )
    inlines = (QuoteItemInline,)


@admin.register(QuoteItem)
class QuoteItemAdmin(admin.ModelAdmin):
    list_display = ("quote", "position", "description", "quantity", "unit_price", "discount_type", "line_total")
    list_filter = ("discount_type", "taxable")
    search_fields = ("quote__quote_number", "description", "catalog_item__name")
    readonly_fields = ("id", "line_subtotal", "discount_amount", "line_total", "created_at", "updated_at", "deleted_at")


@admin.register(QuoteActivityLog)
class QuoteActivityLogAdmin(admin.ModelAdmin):
    list_display = ("quote", "event", "actor", "created_at")
    list_filter = ("event", "created_at")
    search_fields = ("quote__quote_number", "actor__email", "message")
    readonly_fields = ("id", "quote", "actor", "event", "message", "metadata", "created_at", "updated_at", "deleted_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(QuoteAcceptance)
class QuoteAcceptanceAdmin(admin.ModelAdmin):
    list_display = ("quote", "signer_name", "signed_at", "snapshot_sha256")
    search_fields = ("quote__quote_number", "signer_name", "snapshot_sha256")
    readonly_fields = (
        "id", "quote", "signer_name", "signature_strokes", "consent_text",
        "quote_snapshot", "snapshot_sha256", "signed_at", "connection_ip",
        "user_agent", "created_at", "updated_at", "deleted_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
