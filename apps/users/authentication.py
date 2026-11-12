from django.core.exceptions import ValidationError as DjangoValidationError
from django.conf import settings
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.telemetry.models import UserSession
from apps.telemetry.services.session import touch_session


def require_active_session(user, token):
    session_id = token.get("session_id")
    try:
        session = UserSession.objects.filter(
            pk=session_id, user=user, revoked_at__isnull=True
        ).first() if session_id else None
    except DjangoValidationError:
        session = None
    if session is None:
        raise AuthenticationFailed("La sesión fue revocada. Inicia sesión de nuevo.")
    return session


class SessionJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        raw_access = request.COOKIES.get(settings.ACCESS_TOKEN_COOKIE_NAME)
        if self.get_header(request) is not None:
            result = super().authenticate(request)
        elif raw_access:
            # Cookies are sent automatically by browsers, so unsafe requests need CSRF protection.
            SessionAuthentication().enforce_csrf(request)
            token = self.get_validated_token(raw_access)
            result = (self.get_user(token), token)
        else:
            result = None
        if result is None:
            return None
        user, token = result
        if user.deleted_at is not None:
            raise AuthenticationFailed("La cuenta ya no está disponible.")
        session = require_active_session(user, token)
        request.current_session_id = session.pk
        touch_session(session)
        return user, token
