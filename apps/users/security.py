from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.common.audit import get_client_ip, log_security_event
from apps.common.models import SecurityEvent
from apps.users.models import User


LOGIN_LOCKOUT_MESSAGE = "Demasiados intentos fallidos. Intenta nuevamente mas tarde."


def normalize_login_email(raw_email):
    return User.objects.normalize_email(raw_email or "").lower()


def enforce_login_lockout(request):
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
            SecurityEvent.objects.filter(event_type="login_success", metadata__email=email)
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

    failures_in_window = SecurityEvent.objects.filter(
        identity_filter,
        event_type="login_failed",
        created_at__gte=failure_start,
    ).count()
    failures_in_lockout = SecurityEvent.objects.filter(
        identity_filter,
        event_type="login_failed",
        created_at__gte=lockout_start,
    ).count()

    if failures_in_window >= settings.LOGIN_FAILED_LIMIT and failures_in_lockout >= settings.LOGIN_FAILED_LIMIT:
        log_security_event(
            "login_blocked",
            request=request,
            tenant=getattr(user, "tenant", None),
            user=user,
            severity="warning",
            metadata={"email": email},
        )
        raise ValidationError({"detail": LOGIN_LOCKOUT_MESSAGE})
