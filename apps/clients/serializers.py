from rest_framework import serializers

from apps.common.validators import clean_text, validate_nit, validate_phone
from apps.clients.models import Client


class ClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = [
            "id",
            "name",
            "company_name",
            "email",
            "phone",
            "nit",
            "address_line1",
            "address_line2",
            "city",
            "department",
            "country_code",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_name(self, value):
        value = clean_text(value)
        if not value:
            raise serializers.ValidationError("El nombre del cliente es obligatorio.")
        return value

    def validate_nit(self, value):
        return validate_nit(value)

    def validate_phone(self, value):
        return validate_phone(value)

    def validate(self, attrs):
        request = self.context["request"]
        tenant = getattr(request.user, "tenant", None)
        if request.user.is_super_admin:
            tenant = attrs.get("tenant") or tenant
        instance_id = self.instance.pk if self.instance else None
        email = attrs.get("email", getattr(self.instance, "email", "")).strip().lower()
        nit = attrs.get("nit", getattr(self.instance, "nit", "")).strip().upper()
        if tenant and email:
            qs = Client.objects.filter(tenant=tenant, email__iexact=email, deleted_at__isnull=True)
            if instance_id:
                qs = qs.exclude(pk=instance_id)
            if qs.exists():
                raise serializers.ValidationError({"email": ["Ya existe un cliente con este email en tu tenant."]})
        if tenant and nit:
            qs = Client.objects.filter(tenant=tenant, nit__iexact=nit, deleted_at__isnull=True)
            if instance_id:
                qs = qs.exclude(pk=instance_id)
            if qs.exists():
                raise serializers.ValidationError({"nit": ["Ya existe un cliente con este NIT en tu tenant."]})
        attrs["email"] = email
        attrs["nit"] = nit
        return attrs
