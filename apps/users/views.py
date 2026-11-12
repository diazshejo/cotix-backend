import logging

from django.conf import settings
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.generics import CreateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.utils import get_md5_hash_password
from rest_framework_simplejwt.exceptions import TokenError
from drf_spectacular.utils import extend_schema

from apps.common.audit import get_client_ip, log_security_event
from apps.common.permissions import TenantUserAdminPermission
from apps.common.throttles import (
    LoginThrottle,
    PasswordResetThrottle,
    RegisterThrottle,
    VerifyEmailResendThrottle,
    VerifyEmailThrottle,
)
from apps.telemetry.models import AuthEvent, UserSession
from apps.telemetry.services.auth_telemetry import log_auth_event
from apps.telemetry.services.session import (
    compute_device_fingerprint,
    create_session,
    is_known_device,
    touch_session,
)
from apps.telemetry.services.user_agent import parse_user_agent
from apps.tenants.services.plans import QUOTA_MAX_USERS, PlanService
from apps.tenants.models import Tenant
from apps.users.authentication import require_active_session
from apps.users.models import User
from apps.users.serializers import (
    CotixTokenObtainPairSerializer,
    ForgotPasswordSerializer,
    RegisterSerializer,
    ResendVerificationSerializer,
    ResetPasswordSerializer,
    TenantUserCreateSerializer,
    UserSerializer,
    VerifyEmailSerializer,
)
from apps.users.security import enforce_login_lockout, normalize_login_email
from apps.users.services.email import send_password_reset_email, send_verification_email
from apps.users.tokens import generate_token, reset_expiry, verification_expiry

logger = logging.getLogger("apps.users")


def set_refresh_cookie(response, refresh_token):
    response.set_cookie(
        settings.REFRESH_TOKEN_COOKIE_NAME,
        refresh_token,
        max_age=int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
        httponly=True,
        secure=settings.REFRESH_TOKEN_COOKIE_SECURE,
        samesite=settings.REFRESH_TOKEN_COOKIE_SAMESITE,
        path=settings.REFRESH_TOKEN_COOKIE_PATH,
    )


def set_access_cookie(response, access_token):
    response.set_cookie(
        settings.ACCESS_TOKEN_COOKIE_NAME,
        access_token,
        max_age=int(settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds()),
        httponly=True,
        secure=settings.ACCESS_TOKEN_COOKIE_SECURE,
        samesite=settings.ACCESS_TOKEN_COOKIE_SAMESITE,
        path=settings.ACCESS_TOKEN_COOKIE_PATH,
    )


def clear_refresh_cookie(response):
    response.delete_cookie(
        settings.REFRESH_TOKEN_COOKIE_NAME,
        path=settings.REFRESH_TOKEN_COOKIE_PATH,
        samesite=settings.REFRESH_TOKEN_COOKIE_SAMESITE,
    )


class CsrfTokenView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        response = Response({"csrf_token": get_token(request)})
        response["Cache-Control"] = "no-store"
        return response


class RegisterView(CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [RegisterThrottle]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        raw_token = getattr(user, "_raw_verification_token", None)
        if raw_token:
            try:
                send_verification_email(user.email, raw_token)
            except Exception:
                logger.error("Verification email delivery failed for %s", user.email)

        log_auth_event(
            AuthEvent.EventType.REGISTER_SUCCESS,
            request=request,
            user=user,
            tenant=user.tenant,
            metadata={"email": user.email},
        )
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


@method_decorator(csrf_protect, name="dispatch")
class LoginView(TokenObtainPairView):
    serializer_class = CotixTokenObtainPairSerializer
    throttle_classes = [LoginThrottle]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        email = normalize_login_email(request.data.get("email", ""))
        enforce_login_lockout(request)

        user_lookup = User.objects.filter(email__iexact=email).first() if email else None
        if user_lookup and not user_lookup.email_verified:
            log_auth_event(
                AuthEvent.EventType.LOGIN_UNVERIFIED_EMAIL,
                request=request,
                user=user_lookup,
                success=False,
                metadata={"email": email},
            )
            raise ValidationError(
                {"detail": "Debes verificar tu correo antes de ingresar.", "code": "email_not_verified"}
            )

        try:
            response = super().post(request, *args, **kwargs)
        except Exception:
            log_auth_event(
                AuthEvent.EventType.LOGIN_FAILED,
                request=request,
                success=False,
                metadata={"email": email},
            )
            raise

        user = User.objects.filter(email__iexact=email).first()
        response.data.pop("refresh", None)

        # Session tracking and new-device detection
        raw_ua = request.META.get("HTTP_USER_AGENT", "")[:500]
        ua_data = parse_user_agent(raw_ua)
        fingerprint = compute_device_fingerprint(ua_data["browser"], ua_data["os"], raw_ua)
        known_device = is_known_device(user, fingerprint) if user else True
        session = create_session(
            user,
            ip_address=get_client_ip(request),
            browser=ua_data["browser"],
            os=ua_data["os"],
            device_type=ua_data["device_type"],
            user_agent=raw_ua,
        ) if user else None

        if session is None:
            raise AuthenticationFailed("No se pudo crear la sesión. Intenta de nuevo.")
        refresh = CotixTokenObtainPairSerializer.get_token(user)
        refresh["session_id"] = str(session.pk)
        set_access_cookie(response, str(refresh.access_token))
        set_refresh_cookie(response, str(refresh))
        response.data.pop("access", None)
        response.data["ok"] = True

        log_auth_event(
            AuthEvent.EventType.LOGIN_SUCCESS,
            request=request,
            user=user,
            tenant=getattr(user, "tenant", None),
            session=session,
            metadata={"email": email, "is_new_device": not known_device},
        )

        if not known_device and user:
            log_auth_event(
                AuthEvent.EventType.NEW_DEVICE_LOGIN,
                request=request,
                user=user,
                tenant=getattr(user, "tenant", None),
                session=session,
                metadata={"email": email, "is_new_device": True, "risk_factors": ["new_device"]},
            )

        return response


@method_decorator(csrf_protect, name="dispatch")
class RefreshView(TokenRefreshView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        refresh_token = request.COOKIES.get(settings.REFRESH_TOKEN_COOKIE_NAME)
        if not refresh_token:
            return Response({"detail": "Refresh token no encontrado."}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            token = RefreshToken(refresh_token)
            user = User.objects.get(pk=token.get("user_id"), is_active=True, deleted_at__isnull=True)
            session = require_active_session(user, token)
            if api_settings.CHECK_REVOKE_TOKEN and token.get(api_settings.REVOKE_TOKEN_CLAIM) != get_md5_hash_password(user.password):
                raise AuthenticationFailed("Sesión inválida. Inicia sesión de nuevo.")
        except (User.DoesNotExist, TokenError, ValueError, TypeError, DjangoValidationError):
            raise AuthenticationFailed("Sesión inválida. Inicia sesión de nuevo.")
        touch_session(session)
        serializer = TokenRefreshSerializer(data={"refresh": refresh_token})
        serializer.is_valid(raise_exception=True)
        response = Response(serializer.validated_data, status=status.HTTP_200_OK)
        set_access_cookie(response, serializer.validated_data["access"])
        rotated_refresh = serializer.validated_data.get("refresh")
        if rotated_refresh:
            set_refresh_cookie(response, rotated_refresh)
        response.data = {"ok": True}
        return response


@method_decorator(csrf_protect, name="dispatch")
class LogoutView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        raw_refresh = request.COOKIES.get(settings.REFRESH_TOKEN_COOKIE_NAME)
        if raw_refresh:
            try:
                token = RefreshToken(raw_refresh)
                session = UserSession.objects.select_related("user", "user__tenant").filter(
                    pk=token.get("session_id"), user_id=token.get("user_id")
                ).first()
                if session:
                    UserSession.objects.filter(pk=session.pk).update(revoked_at=timezone.now())
                    log_auth_event(
                        AuthEvent.EventType.LOGOUT,
                        request=request,
                        user=session.user,
                        tenant=session.user.tenant,
                        session=session,
                        metadata={"email": session.user.email},
                    )
            except (TokenError, ValueError, TypeError, DjangoValidationError):
                pass
        response = Response({"ok": True})
        clear_refresh_cookie(response)
        response.delete_cookie(settings.ACCESS_TOKEN_COOKIE_NAME, path=settings.ACCESS_TOKEN_COOKIE_PATH, samesite=settings.ACCESS_TOKEN_COOKIE_SAMESITE)
        return response


class VerifyEmailView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [VerifyEmailThrottle]

    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        log_auth_event(
            AuthEvent.EventType.EMAIL_VERIFIED,
            request=request,
            user=user,
            tenant=user.tenant,
            metadata={"email": user.email},
        )
        return Response({"ok": True})


class ResendVerificationView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [VerifyEmailResendThrottle]

    def post(self, request):
        serializer = ResendVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        user = User.objects.filter(email__iexact=email, email_verified=False, is_active=True).first()
        if user:
            raw_token, token_hash = generate_token()
            from apps.users.models import EmailVerificationToken
            EmailVerificationToken.objects.update_or_create(
                user=user,
                defaults={"token_hash": token_hash, "expires_at": verification_expiry(), "used_at": None},
            )
            try:
                send_verification_email(user.email, raw_token)
                log_auth_event(
                    AuthEvent.EventType.EMAIL_VERIFICATION_RESENT,
                    request=request,
                    user=user,
                    metadata={"email": email},
                )
            except Exception:
                logger.error("Resend verification email failed for %s", email)

        return Response({"ok": True})


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PasswordResetThrottle]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        user = User.objects.filter(email__iexact=email, is_active=True, email_verified=True).first()
        if user:
            raw_token, token_hash = generate_token()
            from apps.users.models import PasswordResetToken
            PasswordResetToken.objects.update_or_create(
                user=user,
                defaults={"token_hash": token_hash, "expires_at": reset_expiry(), "used_at": None},
            )
            try:
                send_password_reset_email(user.email, raw_token)
                log_auth_event(
                    AuthEvent.EventType.PASSWORD_RESET_REQUESTED,
                    request=request,
                    user=user,
                    metadata={"email": email},
                )
            except Exception:
                logger.error("Password reset email failed for %s", email)

        return Response({"ok": True})


class ResetPasswordConfirmView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PasswordResetThrottle]

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        log_auth_event(
            AuthEvent.EventType.PASSWORD_RESET_COMPLETED,
            request=request,
            user=user,
            tenant=user.tenant,
            metadata={"email": user.email},
        )
        return Response({"ok": True})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=UserSerializer)
    def get(self, request):
        return Response(UserSerializer(request.user).data)


class TenantUserViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, TenantUserAdminPermission]

    def get_serializer_class(self):
        if self.action == "create":
            return TenantUserCreateSerializer
        return UserSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return User.objects.none()
        queryset = User.objects.filter(deleted_at__isnull=True)
        if self.request.user.is_super_admin:
            return queryset
        return queryset.filter(tenant=self.request.user.tenant)

    def perform_destroy(self, instance):
        instance.soft_delete()

    def perform_update(self, serializer):
        if not self.request.user.is_super_admin and serializer.instance.role == User.Role.SUPER_ADMIN:
            raise ValidationError({"detail": "No puedes modificar usuarios super_admin."})
        old_role = serializer.instance.role
        user = serializer.save()
        if old_role != user.role:
            log_security_event(
                "user_role_changed",
                request=self.request,
                tenant=user.tenant,
                user=self.request.user,
                metadata={"target_user": user.email, "old_role": old_role, "new_role": user.role},
            )

    @transaction.atomic
    def perform_create(self, serializer):
        tenant = getattr(self.request.user, "tenant", None)
        if tenant and not self.request.user.is_super_admin:
            tenant = Tenant.objects.select_for_update().get(pk=tenant.pk)
            PlanService.require_active_trial(tenant)
            current = User.objects.filter(tenant=tenant, deleted_at__isnull=True).count()
            PlanService.require_quota(tenant, QUOTA_MAX_USERS, current)
        user = serializer.save()
        log_security_event(
            "user_created",
            request=self.request,
            tenant=user.tenant,
            user=self.request.user,
            metadata={"target_user": user.email, "role": user.role},
        )
