from decimal import Decimal

from django.db import models

from apps.common.models import BaseModel


class CatalogCategory(BaseModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="catalog_categories")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "name"],
                condition=models.Q(deleted_at__isnull=True),
                name="uq_catalog_category_tenant_name_active",
            )
        ]

    def __str__(self):
        return self.name


class CatalogItem(BaseModel):
    class ItemType(models.TextChoices):
        PRODUCT = "product", "Product"
        SERVICE = "service", "Service"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="catalog_items")
    category = models.ForeignKey(
        CatalogCategory,
        on_delete=models.SET_NULL,
        related_name="items",
        null=True,
        blank=True,
    )
    item_type = models.CharField(max_length=20, choices=ItemType.choices, default=ItemType.PRODUCT)
    sku = models.CharField(max_length=80, blank=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    unit_price = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0.0000"))
    unit = models.CharField(max_length=50, default="unidad")
    currency = models.CharField(max_length=3, default="GTQ")
    taxable = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["tenant", "name"]), models.Index(fields=["tenant", "sku"])]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "sku"],
                condition=~models.Q(sku="") & models.Q(deleted_at__isnull=True),
                name="uq_catalog_item_tenant_sku",
            ),
            models.CheckConstraint(condition=models.Q(unit_price__gte=0), name="ck_catalog_item_unit_price_gte_0"),
            models.CheckConstraint(condition=models.Q(currency__in=["GTQ", "USD"]), name="ck_catalog_item_currency_allowed"),
        ]

    def __str__(self):
        return self.name
