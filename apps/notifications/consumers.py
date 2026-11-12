import asyncio
import time

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from apps.telemetry.models import UserSession
from apps.users.models import User


def notification_group(user_id):
    return f"notifications.user.{user_id.hex}"


@database_sync_to_async
def session_is_active(user_id, session_id):
    user = User.objects.select_related("tenant").filter(pk=user_id, is_active=True, deleted_at__isnull=True).first()
    if user is None:
        return False
    if not user.is_super_admin and not (user.tenant and user.tenant.is_active and user.tenant.status in {"demo", "active"}):
        return False
    return UserSession.objects.filter(pk=session_id, user_id=user_id, revoked_at__isnull=True).exists()


class NotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close(code=4401)
            return
        self.group_name = notification_group(user.pk)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        self.auth_watch = asyncio.create_task(self._watch_session())

    async def disconnect(self, code):
        if hasattr(self, "auth_watch"):
            self.auth_watch.cancel()
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        # This channel is server-to-client only.
        await self.close(code=4400)

    async def notification_changed(self, event):
        await self.send(text_data='{"type":"notification_changed"}')

    async def _watch_session(self):
        try:
            while True:
                remaining = self.scope["expires_at"] - time.time()
                if remaining <= 0:
                    await self.close(code=4401)
                    return
                await asyncio.sleep(min(30, remaining))
                if not await session_is_active(self.scope["user"].pk, self.scope["session_id"]):
                    await self.close(code=4401)
                    return
        except asyncio.CancelledError:
            return
