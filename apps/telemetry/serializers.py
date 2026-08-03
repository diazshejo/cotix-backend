from rest_framework import serializers

from apps.telemetry.models import AuthEvent, UserSession


class UserSessionSerializer(serializers.ModelSerializer):
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = UserSession
        fields = [
            "id",
            "ip_address",
            "browser",
            "os",
            "device_type",
            "login_at",
            "last_seen_at",
            "is_current",
        ]
        read_only_fields = fields

    def get_is_current(self, obj) -> bool:
        current_id = self.context.get("current_session_id")
        return str(obj.pk) == str(current_id) if current_id else False


class AuthEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuthEvent
        fields = [
            "id",
            "event_type",
            "ip_address",
            "country_code",
            "browser",
            "os",
            "device_type",
            "success",
            "risk_score",
            "created_at",
        ]
        read_only_fields = fields
