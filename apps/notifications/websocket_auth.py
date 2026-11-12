from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from apps.common.permissions import tenant_is_operational
from apps.users.authentication import require_active_session


@database_sync_to_async
def authenticate_cookie(raw_access):
    if not raw_access:
        return AnonymousUser(), None, None
    try:
        authentication = JWTAuthentication()
        token = authentication.get_validated_token(raw_access)
        user = authentication.get_user(token)
        if user.deleted_at is not None or not tenant_is_operational(user):
            return AnonymousUser(), None, None
        session = require_active_session(user, token)
        return user, session.pk, int(token["exp"])
    except (AuthenticationFailed, InvalidToken, TokenError, ValueError, TypeError):
        return AnonymousUser(), None, None


class SessionJWTScopeMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        scope = dict(scope)
        user, session_id, expires_at = await authenticate_cookie(
            scope.get("cookies", {}).get(settings.ACCESS_TOKEN_COOKIE_NAME)
        )
        scope["user"] = user
        scope["session_id"] = session_id
        scope["expires_at"] = expires_at
        return await super().__call__(scope, receive, send)
