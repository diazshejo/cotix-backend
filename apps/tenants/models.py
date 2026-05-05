from decimal import Decimal

from django.db import models
from django.db.models import Q

from apps.common.models import BaseModel


class Tenant(BaseModel):
    STATUS_CHOICES = (
        ("demo", "Demo"),
        ("active", "Active"),
        ("suspended", "Suspended"),
        ("cancelled", "Cancelled"),
    )

    slug = models.SlugField(max_length=80, unique=True)
    business_name = models.CharField(max_length=200)
    trade_name = models.CharField(max_length=200, blank=True)
    nit = models.CharField(max_length=20, blank=True)
    tax_regime = models.CharField(max_length=50, blank=True)
    email = models.EmailField()
    phone = models.CharField(max_length=30, blank=True)
    address_line1 = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    department = models.CharField(max_length=100, blank=True)
    country_code = models.CharField(max_length=2, default="GT")
    logo_url = models.URLField(blank=True)
    primary_color = models.CharField(max_length=7, default="#1a6fdb")
    default_currency = models.CharField(max_length=3, default="GTQ")
    tax_label = models.CharField(max_length=20, default="IVA")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("12.00"))
    prices_include_tax = models.BooleanField(default=False)
    quote_prefix = models.CharField(max_length=10, default="COT")
    quote_next_number = models.PositiveIntegerField(default=1)
    default_footer_text = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="demo")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["business_name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(tax_rate__gte=0) & Q(tax_rate__lte=100),
                name="ck_tenant_tax_rate_0_100",
            ),
            models.CheckConstraint(condition=Q(quote_next_number__gte=1), name="ck_tenant_quote_next_number_gte_1"),
            models.CheckConstraint(condition=Q(default_currency__in=["GTQ", "USD"]), name="ck_tenant_currency_allowed"),
        ]

    def __str__(self):
        return self.trade_name or self.business_name
