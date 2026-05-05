from rest_framework import serializers

from apps.common.validators import (
    clean_text,
    validate_currency,
    validate_hex_color,
    validate_nit,
    validate_phone,
    validate_quote_prefix,
    validate_slug,
)
from apps.tenants.models import Tenant


class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = [
            "id",
            "slug",
            "business_name",
            "trade_name",
            "nit",
            "tax_regime",
            "email",
            "phone",
            "address_line1",
            "address_line2",
            "city",
            "department",
            "country_code",
            "logo_url",
            "primary_color",
            "default_currency",
            "tax_label",
            "tax_rate",
            "prices_include_tax",
            "quote_prefix",
            "quote_next_number",
            "default_footer_text",
            "status",
            "is_active",
        ]
        read_only_fields = ["id", "quote_next_number", "status", "is_active"]

    def validate_business_name(self, value):
        value = clean_text(value)
        if len(value) < 3:
            raise serializers.ValidationError("El nombre del tenant debe tener al menos 3 caracteres.")
        return value

    def validate_slug(self, value):
        value = validate_slug(value)
        queryset = Tenant.objects.filter(slug=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("Ya existe un tenant con este slug.")
        return value

    def validate_nit(self, value):
        return validate_nit(value)

    def validate_phone(self, value):
        return validate_phone(value)

    def validate_primary_color(self, value):
        return validate_hex_color(value)

    def validate_default_currency(self, value):
        return validate_currency(value)

    def validate_tax_rate(self, value):
        if value < 0 or value > 100:
            raise serializers.ValidationError("La tasa de impuesto debe estar entre 0 y 100.")
        return value

    def validate_quote_prefix(self, value):
        return validate_quote_prefix(value)
