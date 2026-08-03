from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel


class Notification(BaseModel):
    class Type(models.TextChoices):
        QUOTE_ACCEPTED = "quote_accepted", "Cotización aceptada"
        QUOTE_REJECTED = "quote_rejected", "Cotización rechazada"
        QUOTE_VIEWED   = "quote_viewed",   "Cotización vista"
        QUOTE_SENT     = "quote_sent",     "Cotización enviada"

    user   = models.ForeignKey("users.User",     on_delete=models.CASCADE, related_name="notifications")
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="notifications")
    quote  = models.ForeignKey(
        "quotes.Quote",
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
    )
    notification_type = models.CharField(max_length=30, choices=Type.choices)
    title   = models.CharField(max_length=255)
    message = models.TextField()
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["user", "is_read"]),
        ]

    def __str__(self):
        return f"{self.notification_type} → {self.user_id}"

    def mark_as_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read", "read_at", "updated_at"])

    @property
    def notification_display(self):
        return self.get_notification_type_display()
