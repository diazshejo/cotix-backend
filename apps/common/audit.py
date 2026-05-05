SENSITIVE_METADATA_KEYS = {"password", "token", "access", "refresh", "authorization", "secret"}


def get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def sanitize_metadata(metadata):
    clean = {}
    for key, value in (metadata or {}).items():
        if str(key).lower() in SENSITIVE_METADATA_KEYS:
            continue
        clean[key] = str(value)[:500] if not isinstance(value, (dict, list, int, float, bool, type(None))) else value
    return clean


def log_security_event(
    event_type,
    *,
    request=None,
    tenant=None,
    user=None,
    severity="info",
    metadata=None,
):
    from apps.common.models import SecurityEvent

    if request is not None:
        request_user = getattr(request, "user", None)
        if user is None and getattr(request_user, "is_authenticated", False):
            user = request_user
        if tenant is None and user is not None:
            tenant = getattr(user, "tenant", None)

    return SecurityEvent.objects.create(
        tenant=tenant,
        user=user,
        event_type=event_type,
        severity=severity,
        ip_address=get_client_ip(request) if request is not None else None,
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:1000] if request is not None else "",
        path=request.path[:500] if request is not None else "",
        method=request.method if request is not None else "",
        metadata=sanitize_metadata(metadata),
    )
