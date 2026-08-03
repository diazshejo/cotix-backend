from __future__ import annotations

import logging
from typing import Optional

from django.db import DatabaseError

from apps.telemetry.services.risk import compute_risk_score
from apps.telemetry.services.user_agent import parse_user_agent

logger = logging.getLogger("apps.telemetry")


def _get_client_ip(request) -> Optional[str]:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _extract_request_context(request) -> dict:
    if request is None:
        return {}
    raw_ua = request.META.get("HTTP_USER_AGENT", "")[:500]
    ua = parse_user_agent(raw_ua)
    return {
        "ip_address": _get_client_ip(request),
        "user_agent": raw_ua,
        "browser": ua["browser"],
        "os": ua["os"],
        "device_type": ua["device_type"],
        "ua_extra": {
            "browser_version": ua["browser_version"],
            "is_mobile": ua["is_mobile"],
        },
    }


def log_auth_event(
    event_type: str,
    *,
    request=None,
    user=None,
    tenant=None,
    session=None,
    success: bool = True,
    metadata: Optional[dict] = None,
):
    """
    Create an AuthEvent record.

    Design contract:
    - Never raises; errors are logged so callers are not affected.
    - Intentionally synchronous for MVP.  Move to Celery task when auth event
      volume exceeds ~500 req/s (replace body with: task.delay(...)).
    - Returns the created AuthEvent or None on failure.
    """
    from apps.telemetry.models import AuthEvent

    try:
        ctx = _extract_request_context(request)

        if tenant is None and user is not None:
            tenant = getattr(user, "tenant", None)

        caller_meta = metadata or {}
        event_meta = {
            **ctx.get("ua_extra", {}),
            "geo": {"country_code": None, "city": None},  # async GeoIP placeholder
            **caller_meta,
        }

        risk_factors = caller_meta.get("risk_factors", [])
        is_new_device = caller_meta.get("is_new_device", False)
        risk = compute_risk_score(
            event_type=event_type,
            success=success,
            is_new_device=is_new_device,
            risk_factors=risk_factors,
        )

        return AuthEvent.objects.create(
            user=user,
            tenant=tenant,
            session=session,
            event_type=event_type,
            ip_address=ctx.get("ip_address"),
            browser=ctx.get("browser", ""),
            os=ctx.get("os", ""),
            device_type=ctx.get("device_type", ""),
            user_agent=ctx.get("user_agent", ""),
            success=success,
            risk_score=risk,
            metadata=event_meta,
        )
    except DatabaseError:
        logger.exception("Failed to persist AuthEvent(type=%s)", event_type)
        return None
    except Exception:
        logger.exception("Unexpected error in log_auth_event(type=%s)", event_type)
        return None
