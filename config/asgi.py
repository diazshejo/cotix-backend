"""
ASGI config for config project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import OriginValidator
from channels.sessions import CookieMiddleware
from django.conf import settings
from django.urls import path

from apps.notifications.consumers import NotificationConsumer
from apps.notifications.websocket_auth import SessionJWTScopeMiddleware

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": OriginValidator(
        CookieMiddleware(SessionJWTScopeMiddleware(URLRouter([
            path("api/notifications/ws/", NotificationConsumer.as_asgi()),
        ]))),
        settings.CORS_ALLOWED_ORIGINS,
    ),
})
