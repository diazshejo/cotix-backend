from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.common.audit import get_client_ip
from apps.users.models import User


LOGIN_LOCKOUT_MESSAGE = "Demasiados intentos fallidos. Intenta nuevamente mas tarde."


def normalize_login_email(raw_email):
    return User.objects.normalize_email(raw_email or "").lower()


def enforce_login_lockout(request):
    from apps.telemetry.models import AuthEvent
    from apps.telemetry.services.auth_telemetry import log_auth_event

    email = normalize_login_email(request.data.get("email", ""))
    ip_address = get_client_ip(request)
    if not email and not ip_address:
        return

    user = User.objects.filter(email__iexact=email).first() if email else None
    if user and user.is_super_admin:
        return

    now = timezone.now()
    window_start = now - timedelta(minutes=settings.LOGIN_FAILED_WINDOW_MINUTES)
    lockout_start = now - timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)

    last_success = None
    if email:
        last_success = (
            AuthEvent.objects.filter(
                event_type=AuthEvent.EventType.LOGIN_SUCCESS,
                metadata__email=email,
            )
            .order_by("-created_at")
            .first()
        )

    failure_start = window_start
    if last_success and last_success.created_at > failure_start:
        failure_start = last_success.created_at

    identity_filter = Q()
    if email:
        identity_filter |= Q(metadata__email=email)
    if ip_address:
        identity_filter |= Q(ip_address=ip_address)

    failures_in_window = AuthEvent.objects.filter(
        identity_filter,
        event_type=AuthEvent.EventType.LOGIN_FAILED,
        created_at__gte=failure_start,
    ).count()
    failures_in_lockout = AuthEvent.objects.filter(
        identity_filter,
        event_type=AuthEvent.EventType.LOGIN_FAILED,
        created_at__gte=lockout_start,
    ).count()

    if failures_in_window >= settings.LOGIN_FAILED_LIMIT and failures_in_lockout >= settings.LOGIN_FAILED_LIMIT:
        log_auth_event(
            AuthEvent.EventType.LOGIN_BLOCKED,
            request=request,
            user=user,
            tenant=getattr(user, "tenant", None),
            success=False,
            metadata={"email": email},
        )
        raise ValidationError({"detail": LOGIN_LOCKOUT_MESSAGE})
