from django.db import transaction
from django.utils.text import slugify
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from apps.tenants.models import Tenant
from apps.tenants.serializers import TenantSerializer
from apps.users.models import User
from apps.common.validators import validate_nit


class UserSerializer(serializers.ModelSerializer):
    tenant = TenantSerializer(read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "role", "tenant", "is_active"]
        read_only_fields = ["id", "tenant", "is_active"]

    def validate_role(self, value):
        request = self.context.get("request")
        if value == User.Role.SUPER_ADMIN and (not request or not request.user.is_super_admin):
            raise serializers.ValidationError("No puedes asignar el rol super_admin.")
        return value


class RegisterSerializer(serializers.Serializer):
    business_name = serializers.CharField(max_length=200)
    trade_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    nit = serializers.CharField(max_length=20, required=False, allow_blank=True)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True)

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

    def validate(self, attrs):
        if attrs["password"].lower() == attrs["email"].lower():
            raise serializers.ValidationError({"password": ["La contrasena no puede ser igual al email."]})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        email = validated_data["email"]
        base_slug = slugify(validated_data["trade_name"] or validated_data["business_name"])[:70] or "tenant"
        slug = base_slug
        counter = 2
        while Tenant.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1

        tenant = Tenant.objects.create(
            slug=slug,
            business_name=validated_data["business_name"],
            trade_name=validated_data.get("trade_name", ""),
            nit=validated_data.get("nit", ""),
            email=email,
        )
        return User.objects.create_user(
            email=email,
            password=validated_data["password"],
            first_name=validated_data.get("first_name", ""),
            last_name=validated_data.get("last_name", ""),
            role=User.Role.TENANT_ADMIN,
            tenant=tenant,
            is_staff=False,
        )


class TenantUserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

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
        return User.objects.create_user(password=password, tenant=tenant, **validated_data)


class CotixTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["role"] = user.role
        token["tenant_id"] = str(user.tenant_id) if user.tenant_id else None
        return token
