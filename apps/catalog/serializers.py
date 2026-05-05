from rest_framework import serializers

from apps.catalog.models import CatalogCategory, CatalogItem
from apps.common.validators import clean_text, validate_currency


class CatalogCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = CatalogCategory
        fields = ["id", "name", "description", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_name(self, value):
        value = clean_text(value)
        if not value:
            raise serializers.ValidationError("El nombre de la categoria es obligatorio.")
        request = self.context["request"]
        tenant = request.user.tenant if not request.user.is_super_admin else None
        if tenant:
            qs = CatalogCategory.objects.filter(tenant=tenant, name__iexact=value, deleted_at__isnull=True)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError("Ya existe una categoria con este nombre en tu tenant.")
        return value


class CatalogItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = CatalogItem
        fields = [
            "id",
            "category",
            "item_type",
            "sku",
            "name",
            "description",
            "unit_price",
            "unit",
            "currency",
            "taxable",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_category(self, value):
        request = self.context["request"]
        if value and not request.user.is_super_admin and value.tenant_id != request.user.tenant_id:
            raise serializers.ValidationError("La categoria no pertenece a tu tenant.")
        if value and not value.is_active:
            raise serializers.ValidationError("No puedes usar una categoria inactiva.")
        return value

    def validate_name(self, value):
        value = clean_text(value)
        if not value:
            raise serializers.ValidationError("El nombre del item es obligatorio.")
        return value

    def validate_unit(self, value):
        value = clean_text(value)
        if not value:
            raise serializers.ValidationError("La unidad es obligatoria.")
        return value

    def validate_unit_price(self, value):
        if value < 0:
            raise serializers.ValidationError("El precio unitario no puede ser negativo.")
        return value

    def validate_currency(self, value):
        return validate_currency(value)

    def validate_sku(self, value):
        return clean_text(value).upper()

    def validate(self, attrs):
        request = self.context["request"]
        tenant = getattr(request.user, "tenant", None)
        sku = attrs.get("sku", getattr(self.instance, "sku", "")).strip().upper()
        if tenant and sku:
            qs = CatalogItem.objects.filter(tenant=tenant, sku__iexact=sku, deleted_at__isnull=True)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({"sku": ["Ya existe un item con este SKU en tu tenant."]})
        attrs["sku"] = sku
        return attrs
