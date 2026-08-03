import logging

from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from apps.quotes.models import Quote

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Quote)
def _capture_old_status(sender, instance, **kwargs):
    """Store the current DB status on the instance before saving."""
    if not instance.pk:
        instance._old_status = None
        return
    try:
        instance._old_status = Quote.objects.values_list("status", flat=True).get(pk=instance.pk)
    except Quote.DoesNotExist:
        instance._old_status = None


@receiver(post_save, sender=Quote)
def _handle_status_change(sender, instance, created, **kwargs):
    if created:
        return
    old = getattr(instance, "_old_status", None)
    new = instance.status
    if old == new:
        return

    try:
        from apps.notifications.services import NotificationService

        if new == Quote.Status.ACCEPTED:
            NotificationService.notify_quote_accepted(instance)
        elif new == Quote.Status.REJECTED:
            NotificationService.notify_quote_rejected(instance)
        elif new == Quote.Status.VIEWED:
            NotificationService.notify_quote_viewed(instance)
    except Exception:
        logger.exception("Notification dispatch failed for quote %s (%s → %s)", instance.pk, old, new)
