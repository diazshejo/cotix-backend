from types import SimpleNamespace
from unittest.mock import patch
from asgiref.sync import async_to_sync, sync_to_async
from channels.testing import WebsocketCommunicator

from django.conf import settings
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from apps.notifications.models import Notification
from apps.notifications.services import NotificationService
from apps.telemetry.models import UserSession
from apps.tenants.models import Tenant
from apps.users.models import User
from config.asgi import application


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


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class NotificationSocketTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(slug="socket-tests", business_name="Socket Tests", email="test@example.com")
        self.first = User.objects.create_user(email="first@example.com", password="test-password", tenant=self.tenant)
        self.second = User.objects.create_user(email="second@example.com", password="test-password", tenant=self.tenant)
        self.first_session = UserSession.objects.create(user=self.first, tenant=self.tenant)
        self.second_session = UserSession.objects.create(user=self.second, tenant=self.tenant)

    def _socket(self, user, session, origin="http://localhost:5173"):
        token = AccessToken.for_user(user)
        token["session_id"] = str(session.pk)
        return WebsocketCommunicator(application, "/api/notifications/ws/", headers=[
            (b"origin", origin.encode()),
            (b"cookie", f"{settings.ACCESS_TOKEN_COOKIE_NAME}={token}".encode()),
        ])

    def test_only_notification_owner_receives_event(self):
        async def scenario():
            first = self._socket(self.first, self.first_session)
            second = self._socket(self.second, self.second_session)
            self.assertTrue((await first.connect())[0])
            self.assertTrue((await second.connect())[0])
            try:
                await sync_to_async(Notification.objects.create)(
                    user=self.first, tenant=self.tenant,
                    notification_type=Notification.Type.QUOTE_SENT,
                    title="Cotización", message="Actualizada",
                )
                self.assertEqual(await first.receive_json_from(), {"type": "notification_changed"})
                self.assertTrue(await second.receive_nothing(timeout=0.2))
            finally:
                await first.disconnect()
                await second.disconnect()

        async_to_sync(scenario)()

    def test_rejects_foreign_origin_and_revoked_session(self):
        async def scenario():
            foreign = self._socket(self.first, self.first_session, "http://evil.example")
            self.assertFalse((await foreign.connect())[0])
            anonymous = WebsocketCommunicator(application, "/api/notifications/ws/", headers=[
                (b"origin", b"http://localhost:5173"),
            ])
            self.assertFalse((await anonymous.connect())[0])
            revoked = self._socket(self.second, self.second_session)
            await sync_to_async(UserSession.objects.filter(pk=self.second_session.pk).update)(revoked_at=timezone.now())
            self.assertFalse((await revoked.connect())[0])

        async_to_sync(scenario)()
