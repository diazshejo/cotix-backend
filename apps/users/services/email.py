from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger("apps.users")


def _send(subject: str, to_email: str, template: str, context: dict) -> None:
    html_body = render_to_string(template, context)
    text_body = strip_tags(html_body)
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send()


def send_verification_email(user_email: str, raw_token: str) -> None:
    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:5173")
    expire_hours = getattr(settings, "EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS", 24)
    verify_url = f"{frontend_url}/verificar-email?token={raw_token}"
    try:
        _send(
            subject="Verifica tu correo — Cotix",
            to_email=user_email,
            template="emails/verify_email.html",
            context={"verify_url": verify_url, "expire_hours": expire_hours},
        )
    except Exception:
        logger.exception("Failed to send verification email to %s", user_email)
        raise


def send_password_reset_email(user_email: str, raw_token: str) -> None:
    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:5173")
    expire_minutes = getattr(settings, "PASSWORD_RESET_TOKEN_EXPIRE_MINUTES", 30)
    reset_url = f"{frontend_url}/restablecer-password?token={raw_token}"
    try:
        _send(
            subject="Restablece tu password — Cotix",
            to_email=user_email,
            template="emails/reset_password.html",
            context={"reset_url": reset_url, "expire_minutes": expire_minutes},
        )
    except Exception:
        logger.exception("Failed to send password reset email to %s", user_email)
        raise
