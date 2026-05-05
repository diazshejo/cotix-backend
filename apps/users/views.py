from django.conf import settings
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.generics import CreateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from drf_spectacular.utils import extend_schema

from apps.common.audit import log_security_event
from apps.common.permissions import TenantUserAdminPermission
from apps.common.throttles import LoginThrottle, RegisterThrottle
from apps.users.models import User
from apps.users.serializers import (
    CotixTokenObtainPairSerializer,
    RegisterSerializer,
    TenantUserCreateSerializer,
    UserSerializer,
)
from apps.users.security import enforce_login_lockout, normalize_login_email


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


def clear_refresh_cookie(response):
    response.delete_cookie(
        settings.REFRESH_TOKEN_COOKIE_NAME,
        path=settings.REFRESH_TOKEN_COOKIE_PATH,
        samesite=settings.REFRESH_TOKEN_COOKIE_SAMESITE,
    )


class RegisterView(CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    throttle_classes = [RegisterThrottle]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        log_security_event(
            "register_success",
            request=request,
            tenant=user.tenant,
            user=user,
            metadata={"email": user.email},
        )
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class LoginView(TokenObtainPairView):
    serializer_class = CotixTokenObtainPairSerializer
    throttle_classes = [LoginThrottle]

    def post(self, request, *args, **kwargs):
        email = normalize_login_email(request.data.get("email", ""))
        enforce_login_lockout(request)
        try:
            response = super().post(request, *args, **kwargs)
        except Exception:
            log_security_event(
                "login_failed",
                request=request,
                severity="warning",
                metadata={"email": email},
            )
            raise
        user = User.objects.filter(email__iexact=email).first()
        refresh_token = response.data.pop("refresh", None)
        if refresh_token:
            set_refresh_cookie(response, refresh_token)
        log_security_event(
            "login_success",
            request=request,
            tenant=getattr(user, "tenant", None),
            user=user,
            metadata={"email": email},
        )
        return response


class RefreshView(TokenRefreshView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        refresh_token = request.COOKIES.get(settings.REFRESH_TOKEN_COOKIE_NAME)
        if not refresh_token:
            return Response({"detail": "Refresh token no encontrado."}, status=status.HTTP_401_UNAUTHORIZED)

        serializer = TokenRefreshSerializer(data={"refresh": refresh_token})
        serializer.is_valid(raise_exception=True)
        response = Response(serializer.validated_data, status=status.HTTP_200_OK)
        rotated_refresh = serializer.validated_data.get("refresh")
        if rotated_refresh:
            set_refresh_cookie(response, rotated_refresh)
        response.data.pop("refresh", None)
        return response


class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        if request.user.is_authenticated:
            log_security_event(
                "logout",
                request=request,
                tenant=getattr(request.user, "tenant", None),
                user=request.user,
                metadata={"email": request.user.email},
            )
        response = Response({"ok": True})
        clear_refresh_cookie(response)
        return response


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = normalize_login_email(request.data.get("email", ""))
        if email:
            log_security_event(
                "password_reset_requested",
                request=request,
                severity="info",
                metadata={"email": email},
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

    def perform_create(self, serializer):
        user = serializer.save()
        log_security_event(
            "user_created",
            request=self.request,
            tenant=user.tenant,
            user=self.request.user,
            metadata={"target_user": user.email, "role": user.role},
        )
