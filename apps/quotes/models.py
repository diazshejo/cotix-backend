import secrets
from decimal import Decimal

from django.db import models

from apps.common.models import BaseModel


def make_public_token():
    return secrets.token_hex(64)


class Quote(BaseModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        VIEWED = "viewed", "Viewed"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        CHANGES_REQUESTED = "changes_requested", "Changes requested"
        EXPIRED = "expired", "Expired"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="quotes")
    quote_number = models.CharField(max_length=40)
    client = models.ForeignKey("clients.Client", on_delete=models.PROTECT, related_name="quotes")
    created_by = models.ForeignKey("users.User", on_delete=models.PROTECT, related_name="created_quotes")
    assigned_to = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        related_name="assigned_quotes",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=300)
    issue_date = models.DateField()
    expiry_date = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="GTQ")
    internal_notes = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    terms_conditions = models.TextField(blank=True)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    discount_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    taxable_base = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    tax_rate_snapshot = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("12.00"))
    prices_include_tax_snapshot = models.BooleanField(default=False)
    total_tax = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    public_token = models.CharField(max_length=128, unique=True, default=make_public_token)
    pdf_generated_at = models.DateTimeField(null=True, blank=True)
    client_first_viewed_at = models.DateTimeField(null=True, blank=True)
    client_responded_at = models.DateTimeField(null=True, blank=True)
    change_request_message = models.TextField(blank=True)
    revision_of = models.ForeignKey(
        "self", on_delete=models.PROTECT, related_name="revisions", null=True, blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "quote_number"]),
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["public_token"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "quote_number"], name="uq_quote_tenant_number"),
            models.CheckConstraint(condition=models.Q(subtotal__gte=0), name="ck_quote_subtotal_gte_0"),
            models.CheckConstraint(condition=models.Q(discount_total__gte=0), name="ck_quote_discount_total_gte_0"),
            models.CheckConstraint(condition=models.Q(taxable_base__gte=0), name="ck_quote_taxable_base_gte_0"),
            models.CheckConstraint(condition=models.Q(total_tax__gte=0), name="ck_quote_total_tax_gte_0"),
            models.CheckConstraint(condition=models.Q(tax_rate_snapshot__gte=0) & models.Q(tax_rate_snapshot__lte=100), name="ck_quote_tax_rate_0_100"),
            models.CheckConstraint(condition=models.Q(currency__in=["GTQ", "USD"]), name="ck_quote_currency_allowed"),
        ]

    def __str__(self):
        return self.quote_number


class QuoteItem(BaseModel):
    class DiscountType(models.TextChoices):
        NONE = "none", "None"
        PERCENT = "percent", "Percent"
        FIXED = "fixed", "Fixed"

    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="items")
    catalog_item = models.ForeignKey(
        "catalog.CatalogItem",
        on_delete=models.SET_NULL,
        related_name="quote_items",
        null=True,
        blank=True,
    )
    position = models.PositiveSmallIntegerField(default=1)
    description = models.TextField()
    quantity = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("1.0000"))
    unit = models.CharField(max_length=50, default="unidad")
    unit_price = models.DecimalField(max_digits=14, decimal_places=4)
    discount_type = models.CharField(max_length=10, choices=DiscountType.choices, default=DiscountType.NONE)
    discount_value = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.0000"))
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    line_subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    line_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    taxable = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="ck_quote_item_quantity_gt_0"),
            models.CheckConstraint(condition=models.Q(unit_price__gte=0), name="ck_quote_item_unit_price_gte_0"),
            models.CheckConstraint(condition=models.Q(discount_value__gte=0), name="ck_quote_item_discount_value_gte_0"),
            models.CheckConstraint(condition=models.Q(discount_amount__gte=0), name="ck_quote_item_discount_amount_gte_0"),
            models.CheckConstraint(condition=models.Q(line_subtotal__gte=0), name="ck_quote_item_line_subtotal_gte_0"),
            models.CheckConstraint(condition=models.Q(line_total__gte=0), name="ck_quote_item_line_total_gte_0"),
        ]

    def __str__(self):
        return f"{self.quote.quote_number} - {self.position}"


class QuoteActivityLog(BaseModel):
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="activity")
    actor = models.ForeignKey("users.User", on_delete=models.SET_NULL, null=True, blank=True)
    event = models.CharField(max_length=50)
    message = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.quote.quote_number} {self.event}"


class QuoteAcceptance(BaseModel):
    """Evidence of explicit electronic acceptance of one immutable quote revision."""

    quote = models.OneToOneField(Quote, on_delete=models.PROTECT, related_name="acceptance")
    signer_name = models.CharField(max_length=120)
    signature_strokes = models.JSONField()
    consent_text = models.TextField()
    quote_snapshot = models.JSONField()
    snapshot_sha256 = models.CharField(max_length=64)
    signed_at = models.DateTimeField()
    connection_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)

    def __str__(self):
        return f"{self.quote.quote_number} signed by {self.signer_name}"
