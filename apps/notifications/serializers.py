from rest_framework import serializers

from apps.notifications.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    quote_number = serializers.CharField(source="quote.quote_number", read_only=True, default=None)
    quote_id = serializers.UUIDField(source="quote.id", read_only=True, default=None)

    class Meta:
        model = Notification
        fields = [
            "id",
            "notification_type",
            "title",
            "message",
            "is_read",
            "read_at",
            "created_at",
            "quote_id",
            "quote_number",
        ]
        read_only_fields = fields
