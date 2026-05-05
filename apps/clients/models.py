from django.db import models

from apps.common.models import BaseModel


class Client(BaseModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="clients")
    name = models.CharField(max_length=200)
    company_name = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    nit = models.CharField(max_length=20, blank=True)
    address_line1 = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    department = models.CharField(max_length=100, blank=True)
    country_code = models.CharField(max_length=2, default="GT")
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["tenant", "name"])]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "email"],
                condition=~models.Q(email="") & models.Q(deleted_at__isnull=True),
                name="uq_client_tenant_email_active",
            ),
            models.UniqueConstraint(
                fields=["tenant", "nit"],
                condition=~models.Q(nit="") & models.Q(deleted_at__isnull=True),
                name="uq_client_tenant_nit_active",
            ),
        ]

    def __str__(self):
        return self.company_name or self.name
