import secrets

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from apps.common.validators import validate_nit
from apps.tenants.models import Tenant
from apps.tenants.serializers import TenantSerializer
from apps.users.models import EmailVerificationToken, PasswordResetToken, User
from apps.users.tokens import generate_token, hash_token, reset_expiry, verification_expiry

PASSWORD_MIN_LENGTH = 8
# 128-char cap stops slow-hash attacks (PBKDF2 has no internal truncation unlike bcrypt).
PASSWORD_MAX_LENGTH = 128
MAX_SLUG_ATTEMPTS = 10


def _run_password_validators(password: str, user_stub: User) -> None:
    """Run all AUTH_PASSWORD_VALIDATORS and convert DjangoValidationError to DRF."""
    try:
        validate_password(password, user=user_stub)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(list(exc.messages))


class UserSerializer(serializers.ModelSerializer):
    tenant = TenantSerializer(read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "role", "tenant", "is_active", "email_verified"]
        read_only_fields = ["id", "tenant", "is_active", "email_verified"]

    def validate_role(self, value):
        request = self.context.get("request")
        if value == User.Role.SUPER_ADMIN and (not request or not request.user.is_super_admin):
            raise serializers.ValidationError("No puedes asignar el rol super_admin.")
        return value


class RegisterSerializer(serializers.Serializer):
    business_name = serializers.CharField(max_length=200)
    trade_name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    nit = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    email = serializers.EmailField()
    password = serializers.CharField(
        write_only=True,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
    )
    first_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")

    def validate_email(self, value):
        value = User.objects.normalize_email(value)
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Ya existe un usuario con este email.")
        return value

    def validate_business_name(self, value):
        value = value.strip()
        if len(value) < 3:
            raise serializers.ValidationError("El nombre de la empresa debe tener al menos 3 caracteres.")
        return value

    def validate_nit(self, value):
        return validate_nit(value)

    def validate_password(self, value):
        user_stub = User(email=self.initial_data.get("email", ""))
        _run_password_validators(value, user_stub)
        return value

    def validate(self, attrs):
        if attrs["password"].lower() == attrs["email"].lower():
            raise serializers.ValidationError({"password": ["La contrasena no puede ser igual al email."]})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        email = validated_data["email"]
        trade_name = validated_data.get("trade_name", "")
        base_slug = slugify(trade_name or validated_data["business_name"])[:70] or "tenant"
        slug = base_slug
        counter = 2
        while Tenant.objects.filter(slug=slug).exists():
            if counter > MAX_SLUG_ATTEMPTS:
                slug = f"{base_slug[:60]}-{secrets.token_hex(4)}"
                break
            slug = f"{base_slug}-{counter}"
            counter += 1

        tenant = Tenant.objects.create(
            slug=slug,
            business_name=validated_data["business_name"],
            trade_name=trade_name,
            nit=validated_data.get("nit", ""),
            email=email,
        )
        user = User.objects.create_user(
            email=email,
            password=validated_data["password"],
            first_name=validated_data.get("first_name", ""),
            last_name=validated_data.get("last_name", ""),
            role=User.Role.TENANT_ADMIN,
            tenant=tenant,
            is_staff=False,
            email_verified=False,
        )
        raw_token, token_hash_val = generate_token()
        EmailVerificationToken.objects.create(user=user, token_hash=token_hash_val)
        # Attach raw token to user instance so the view can send the email
        user._raw_verification_token = raw_token
        return user


class TenantUserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
    )

    class Meta:
        model = User
        fields = ["id", "email", "password", "first_name", "last_name", "role", "is_active"]
        read_only_fields = ["id", "is_active"]

    def validate_role(self, value):
        if value == User.Role.SUPER_ADMIN:
            raise serializers.ValidationError("No puedes crear usuarios super_admin desde la API del tenant.")
        return value

    def validate_email(self, value):
        value = User.objects.normalize_email(value)
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Ya existe un usuario con este email.")
        return value

    def validate_password(self, value):
        user_stub = User(email=self.initial_data.get("email", ""))
        _run_password_validators(value, user_stub)
        return value

    def validate(self, attrs):
        request = self.context["request"]
        if not request.user.is_super_admin and not request.user.tenant_id:
            raise serializers.ValidationError("Solo super_admin puede existir sin tenant.")
        if attrs["password"].lower() == attrs["email"].lower():
            raise serializers.ValidationError({"password": ["La contrasena no puede ser igual al email."]})
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        tenant = self.context["request"].user.tenant
        return User.objects.create_user(
            password=password,
            tenant=tenant,
            email_verified=True,
            **validated_data,
        )


class CotixTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["role"] = user.role
        token["tenant_id"] = str(user.tenant_id) if user.tenant_id else None
        return token


class VerifyEmailSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=200, write_only=True)

    def validate_token(self, value):
        try:
            obj = EmailVerificationToken.objects.select_related("user").get(token_hash=hash_token(value))
        except EmailVerificationToken.DoesNotExist:
            raise serializers.ValidationError("El token de verificacion no es valido.")
        if not obj.is_valid():
            raise serializers.ValidationError("El token de verificacion ha expirado o ya fue utilizado.")
        self._token_obj = obj
        return value

    @transaction.atomic
    def save(self) -> User:
        obj = self._token_obj
        obj.mark_used()
        obj.user.email_verified = True
        obj.user.save(update_fields=["email_verified", "updated_at"])
        return obj.user


class ResendVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return User.objects.normalize_email(value)


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return User.objects.normalize_email(value)


class ResetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=200, write_only=True)
    password = serializers.CharField(
        write_only=True,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
    )
    confirm_password = serializers.CharField(write_only=True, min_length=PASSWORD_MIN_LENGTH)

    def validate_token(self, value):
        try:
            obj = PasswordResetToken.objects.select_related("user").get(token_hash=hash_token(value))
        except PasswordResetToken.DoesNotExist:
            raise serializers.ValidationError("El token de recuperacion no es valido.")
        if not obj.is_valid():
            raise serializers.ValidationError("El token de recuperacion ha expirado o ya fue utilizado.")
        self._token_obj = obj
        return value

    def validate(self, attrs):
        password = attrs.get("password")
        confirm = attrs.get("confirm_password")

        if password and confirm and password != confirm:
            raise serializers.ValidationError({"confirm_password": ["Los passwords no coinciden."]})

        token_obj = getattr(self, "_token_obj", None)
        if password and token_obj:
            if password.lower() == token_obj.user.email.lower():
                raise serializers.ValidationError({"password": ["La contrasena no puede ser igual al email."]})
            try:
                validate_password(password, user=token_obj.user)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({"password": list(exc.messages)})

        return attrs

    @transaction.atomic
    def save(self) -> User:
        user = self._token_obj.user
        user.set_password(self.validated_data["password"])
        user.save(update_fields=["password", "updated_at"])
        self._token_obj.mark_used()
        return user
