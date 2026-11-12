from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.notifications.models import Notification
from apps.notifications.realtime import publish_notification_change


@receiver(post_save, sender=Notification)
def publish_notification_change_after_save(sender, instance, **kwargs):
    publish_notification_change(instance.user_id)
