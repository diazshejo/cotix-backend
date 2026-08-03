import logging
from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.telemetry.models import AuthEvent, UserSession
from apps.telemetry.serializers import AuthEventSerializer, UserSessionSerializer
from apps.telemetry.services.session import revoke_all_user_sessions, revoke_session

logger = logging.getLogger("apps.telemetry")

_SESSION_WINDOW_DAYS = 30


class ActiveSessionsView(APIView):
    """
    GET  /api/telemetry/sessions/           — list active sessions
    DELETE /api/telemetry/sessions/         — revoke all OTHER sessions
    DELETE /api/telemetry/sessions/{id}/   — revoke a specific session
    """

    permission_classes = [IsAuthenticated]

    def _active_qs(self, user):
        cutoff = timezone.now() - timedelta(days=_SESSION_WINDOW_DAYS)
        return UserSession.objects.filter(
            user=user,
            revoked_at__isnull=True,
            login_at__gte=cutoff,
        ).order_by("-last_seen_at")

    def get(self, request):
        sessions = self._active_qs(request.user)[:20]
        current_session_id = getattr(request, "current_session_id", None)
        serializer = UserSessionSerializer(
            sessions,
            many=True,
            context={"current_session_id": current_session_id},
        )
        return Response(serializer.data)

    def delete(self, request, session_id=None):
        if session_id is not None:
            try:
                session = UserSession.objects.get(pk=session_id, user=request.user)
            except UserSession.DoesNotExist:
                return Response({"detail": "Sesion no encontrada."}, status=status.HTTP_404_NOT_FOUND)
            revoke_session(session)
            return Response({"revoked": 1})

        # Revoke all except the current one
        current_id = getattr(request, "current_session_id", None)
        current_session = UserSession.objects.filter(pk=current_id).first() if current_id else None
        count = revoke_all_user_sessions(request.user, exclude_session=current_session)
        return Response({"revoked": count})


class AuthEventListView(APIView):
    """GET /api/telemetry/auth-events/ — recent auth events for the current user."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        cutoff = timezone.now() - timedelta(days=90)
        events = (
            AuthEvent.objects.filter(user=request.user, created_at__gte=cutoff)
            .order_by("-created_at")[:50]
        )
        return Response(AuthEventSerializer(events, many=True).data)


class FailedLoginSummaryView(APIView):
    """
    GET /api/telemetry/failed-logins/
    Tenant admin: recent failed logins for the tenant (security monitoring).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_authenticated:
            return Response(status=status.HTTP_403_FORBIDDEN)

        cutoff = timezone.now() - timedelta(hours=24)

        if request.user.is_super_admin:
            qs = AuthEvent.objects.filter(
                event_type=AuthEvent.EventType.LOGIN_FAILED,
                created_at__gte=cutoff,
                success=False,
            )
        else:
            if not request.user.tenant_id:
                return Response(status=status.HTTP_403_FORBIDDEN)
            qs = AuthEvent.objects.filter(
                tenant=request.user.tenant,
                event_type=AuthEvent.EventType.LOGIN_FAILED,
                created_at__gte=cutoff,
                success=False,
            )

        events = qs.order_by("-created_at")[:100]
        return Response(AuthEventSerializer(events, many=True).data)
