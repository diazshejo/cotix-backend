import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

from apps.notifications.consumers import notification_group

logger = logging.getLogger(__name__)


def publish_notification_change(user_id):
    """Wake the user's browsers after the database change commits."""
    def publish():
        try:
            async_to_sync(get_channel_layer().group_send)(
                notification_group(user_id), {"type": "notification.changed"},
            )
        except Exception:
            # Database notifications remain available through the REST fallback.
            logger.exception("Real-time notification delivery failed for user %s", user_id)

    transaction.on_commit(publish)
