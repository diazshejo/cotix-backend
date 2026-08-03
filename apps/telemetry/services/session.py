from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from typing import Optional

from django.db import DatabaseError
from django.utils import timezone

logger = logging.getLogger("apps.telemetry")


def compute_device_fingerprint(browser: str, os: str, user_agent: str) -> str:
    """SHA-256[:32] of the device signature.  Deterministic, anonymised."""
    raw = f"{browser}|{os}|{user_agent[:200]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def is_known_device(user, fingerprint: str) -> bool:
    """True if user has a non-revoked session from this device fingerprint."""
    if not fingerprint or user is None:
        return False
    from apps.telemetry.models import UserSession
    return UserSession.objects.filter(
        user=user,
        device_fingerprint=fingerprint,
        revoked_at__isnull=True,
    ).exists()


def create_session(
    user,
    *,
    ip_address: Optional[str] = None,
    browser: str = "",
    os: str = "",
    device_type: str = "",
    user_agent: str = "",
    metadata: Optional[dict] = None,
):
    """
    Create a new UserSession.  Never raises — returns None on DB errors.
    """
    from apps.telemetry.models import UserSession

    try:
        fingerprint = compute_device_fingerprint(browser, os, user_agent)
        return UserSession.objects.create(
            user=user,
            tenant=getattr(user, "tenant", None),
            ip_address=ip_address,
            browser=browser,
            os=os,
            device_type=device_type,
            user_agent=user_agent,
            device_fingerprint=fingerprint,
            metadata=metadata or {},
        )
    except DatabaseError:
        logger.exception("Failed to create UserSession for user=%s", getattr(user, "pk", "?"))
        return None


def touch_session(session, min_interval_seconds: int = 300) -> None:
    """
    Update last_seen_at only if more than min_interval_seconds have elapsed.
    Single UPDATE with WHERE clause — avoids unnecessary writes on every refresh.
    """
    from apps.telemetry.models import UserSession

    threshold = timezone.now() - timedelta(seconds=min_interval_seconds)
    UserSession.objects.filter(
        pk=session.pk,
        last_seen_at__lt=threshold,
    ).update(last_seen_at=timezone.now())


def revoke_session(session) -> None:
    """Mark a single session as revoked."""
    from apps.telemetry.models import UserSession

    UserSession.objects.filter(pk=session.pk).update(revoked_at=timezone.now())


def revoke_all_user_sessions(user, exclude_session=None) -> int:
    """Revoke all active sessions for a user. Returns the count revoked."""
    from apps.telemetry.models import UserSession

    qs = UserSession.objects.filter(user=user, revoked_at__isnull=True)
    if exclude_session is not None:
        qs = qs.exclude(pk=exclude_session.pk)
    return qs.update(revoked_at=timezone.now())
