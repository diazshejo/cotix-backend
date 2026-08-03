import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


class NotificationService:

    @staticmethod
    def notify_quote_accepted(quote):
        user = quote.created_by
        notification = NotificationService._create(
            user=user,
            tenant=quote.tenant,
            quote=quote,
            notification_type="quote_accepted",
            title=f"Cotización {quote.quote_number} aceptada",
            message=(
                f"{quote.client.company_name or quote.client.name} "
                f"aceptó la cotización {quote.quote_number}."
            ),
        )
        NotificationService._send_email(
            user_email=user.email,
            subject=f"✓ Cotización {quote.quote_number} aceptada",
            template="notifications/email/quote_accepted.html",
            context={
                "user": user,
                "quote": quote,
                "client_name": quote.client.company_name or quote.client.name,
                "quote_url": f"{settings.FRONTEND_URL}/quotes/{quote.id}",
            },
        )
        return notification

    @staticmethod
    def notify_quote_rejected(quote):
        user = quote.created_by
        notification = NotificationService._create(
            user=user,
            tenant=quote.tenant,
            quote=quote,
            notification_type="quote_rejected",
            title=f"Cotización {quote.quote_number} rechazada",
            message=(
                f"{quote.client.company_name or quote.client.name} "
                f"rechazó la cotización {quote.quote_number}."
            ),
        )
        NotificationService._send_email(
            user_email=user.email,
            subject=f"✗ Cotización {quote.quote_number} rechazada",
            template="notifications/email/quote_rejected.html",
            context={
                "user": user,
                "quote": quote,
                "client_name": quote.client.company_name or quote.client.name,
                "quote_url": f"{settings.FRONTEND_URL}/quotes/{quote.id}",
            },
        )
        return notification

    @staticmethod
    def notify_quote_viewed(quote):
        from apps.notifications.models import Notification

        already = Notification.objects.filter(
            quote=quote,
            notification_type="quote_viewed",
        ).exists()
        if already:
            return None

        return NotificationService._create(
            user=quote.created_by,
            tenant=quote.tenant,
            quote=quote,
            notification_type="quote_viewed",
            title=f"Cotización {quote.quote_number} vista",
            message=(
                f"{quote.client.company_name or quote.client.name} "
                f"abrió la cotización {quote.quote_number} por primera vez."
            ),
        )

    @staticmethod
    def _create(*, user, tenant, quote, notification_type, title, message):
        from apps.notifications.models import Notification

        return Notification.objects.create(
            user=user,
            tenant=tenant,
            quote=quote,
            notification_type=notification_type,
            title=title,
            message=message,
        )

    @staticmethod
    def _send_email(*, user_email, subject, template, context):
        if not user_email:
            return
        try:
            html_body = render_to_string(template, context)
            text_body = strip_tags(html_body)
            send_mail(
                subject=subject,
                message=text_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user_email],
                html_message=html_body,
                fail_silently=True,
            )
        except Exception:
            logger.exception("Failed to send notification email to %s", user_email)
