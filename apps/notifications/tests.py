from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.notifications.services import NotificationService


class NotificationLinkTests(SimpleTestCase):
    @override_settings(FRONTEND_URL="https://cotix.example/")
    def test_response_emails_link_to_real_quote_route(self):
        quote = SimpleNamespace(
            id="quote-id",
            quote_number="C-1",
            created_by=SimpleNamespace(email="seller@example.com"),
            tenant=object(),
            client=SimpleNamespace(company_name="", name="Cliente"),
        )
        with patch.object(NotificationService, "_create"), patch.object(NotificationService, "_send_email") as send:
            NotificationService.notify_quote_accepted(quote)
            self.assertEqual(send.call_args.kwargs["context"]["quote_url"], "https://cotix.example/app/quotes/quote-id")
            NotificationService.notify_quote_rejected(quote)
            self.assertEqual(send.call_args.kwargs["context"]["quote_url"], "https://cotix.example/app/quotes/quote-id")
