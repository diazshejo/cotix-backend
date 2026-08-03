import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

_TOKEN_BYTE_LENGTH = 32  # 256 bits of entropy


def generate_token() -> tuple[str, str]:
    """Return (raw_token, token_hash). Send raw in email; store hash in DB."""
    raw = secrets.token_urlsafe(_TOKEN_BYTE_LENGTH)
    return raw, _sha256(raw)


def hash_token(raw: str) -> str:
    return _sha256(raw)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verification_expiry():
    hours = getattr(settings, "EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS", 24)
    return timezone.now() + timedelta(hours=hours)


def reset_expiry():
    minutes = getattr(settings, "PASSWORD_RESET_TOKEN_EXPIRE_MINUTES", 30)
    return timezone.now() + timedelta(minutes=minutes)
