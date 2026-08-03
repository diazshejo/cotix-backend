from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from apps.common.audit import log_security_event
from apps.quotes.models import Quote, QuoteActivityLog, QuoteItem
from apps.quotes.services import (
    is_quote_expired,
    log_quote_activity,
    next_quote_number,
    recalculate_quote,
    validate_status_transition,
)
from apps.common.validators import clean_text, validate_currency


class QuoteItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuoteItem
        fields = [
            "id",
            "catalog_item",
            "position",
            "description",
            "quantity",
            "unit",
            "unit_price",
            "discount_type",
            "discount_value",
            "discount_amount",
            "line_subtotal",
            "line_total",
            "taxable",
        ]
        read_only_fields = ["id", "discount_amount", "line_subtotal", "line_total"]
        extra_kwargs = {
            "description": {"required": False, "allow_blank": True},
            "unit": {"required": False},
            "unit_price": {"required": False},
        }

    def validate_description(self, value):
        value = clean_text(value)
        if not value:
            raise serializers.ValidationError("La descripcion de la linea es obligatoria.")
        return value

    def validate_quantity(self, value):
        if value <= 0:
            raise serializers.ValidationError("La cantidad debe ser mayor a cero.")
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

    def validate_discount_value(self, value):
        if value < 0:
            raise serializers.ValidationError("El descuento no puede ser negativo.")
        return value

    def validate(self, attrs):
        discount_type = attrs.get("discount_type", QuoteItem.DiscountType.NONE)
        discount_value = attrs.get("discount_value", 0)
        quantity = attrs.get("quantity", 0)
        unit_price = attrs.get("unit_price", 0)
        if discount_type == QuoteItem.DiscountType.PERCENT and discount_value > 100:
            raise serializers.ValidationError({"discount_value": ["El descuento porcentual debe estar entre 0 y 100."]})
        if discount_type == QuoteItem.DiscountType.FIXED and quantity and unit_price and discount_value > (quantity * unit_price):
            raise serializers.ValidationError({"discount_value": ["El descuento fijo no puede superar el subtotal de la linea."]})
        return attrs


class QuoteListSerializer(serializers.ModelSerializer):
    """Serializer ligero para la lista — sin items anidados."""
    client_name = serializers.CharField(source="client.name", read_only=True)

    class Meta:
        model = Quote
        fields = [
            "id",
            "quote_number",
            "title",
            "status",
            "client",
            "client_name",
            "issue_date",
            "expiry_date",
            "total",
            "currency",
            "public_token",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class QuoteSerializer(serializers.ModelSerializer):
    items = QuoteItemSerializer(many=True, required=True)
    company_name = serializers.SerializerMethodField(read_only=True)
    company_city = serializers.SerializerMethodField(read_only=True)
    company_department = serializers.SerializerMethodField(read_only=True)
    company_logo_url = serializers.SerializerMethodField(read_only=True)
    company_primary_color = serializers.SerializerMethodField(read_only=True)
    company_website = serializers.SerializerMethodField(read_only=True)

    def get_company_name(self, obj):
        return obj.tenant.trade_name or obj.tenant.business_name if obj.tenant_id else ""

    def get_company_city(self, obj):
        return obj.tenant.city if obj.tenant_id else ""

    def get_company_department(self, obj):
        return obj.tenant.department if obj.tenant_id else ""

    def get_company_logo_url(self, obj):
        return obj.tenant.logo_url if obj.tenant_id else ""

    def get_company_primary_color(self, obj):
        return obj.tenant.primary_color if obj.tenant_id else "#1a6fdb"

    def get_company_website(self, obj):
        return obj.tenant.website if obj.tenant_id else ""

    class Meta:
        model = Quote
        fields = [
            "id",
            "quote_number",
            "client",
            "assigned_to",
            "title",
            "issue_date",
            "expiry_date",
            "currency",
            "internal_notes",
            "notes",
            "terms_conditions",
            "subtotal",
            "discount_total",
            "taxable_base",
            "tax_rate_snapshot",
            "total_tax",
            "total",
            "status",
            "public_token",
            "pdf_generated_at",
            "client_first_viewed_at",
            "client_responded_at",
            "company_name",
            "company_city",
            "company_department",
            "company_logo_url",
            "company_primary_color",
            "company_website",
            "items",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "quote_number",
            "subtotal",
            "discount_total",
            "taxable_base",
            "tax_rate_snapshot",
            "total_tax",
            "total",
            "status",
            "public_token",
            "pdf_generated_at",
            "client_first_viewed_at",
            "client_responded_at",
            "created_at",
            "updated_at",
        ]

    def validate_currency(self, value):
        return validate_currency(value)

    def validate_client(self, value):
        request = self.context["request"]
        if not request.user.is_super_admin and value.tenant_id != request.user.tenant_id:
            raise serializers.ValidationError("El cliente no pertenece a tu tenant.")
        if not value.is_active or value.deleted_at:
            raise serializers.ValidationError("No puedes cotizar a un cliente inactivo.")
        return value

    def validate_assigned_to(self, value):
        request = self.context["request"]
        if value and not request.user.is_super_admin and value.tenant_id != request.user.tenant_id:
            raise serializers.ValidationError("El usuario asignado no pertenece a tu tenant.")
        if value and not value.is_active:
            raise serializers.ValidationError("No puedes asignar la cotizacion a un usuario inactivo.")
        return value

    def validate(self, attrs):
        if self.instance and self.instance.status != Quote.Status.DRAFT:
            raise serializers.ValidationError({"detail": "Solo puedes editar cotizaciones en draft."})
        items = attrs.get("items")
        if self.instance is None and not items:
            raise serializers.ValidationError({"detail": "No puedes crear una cotizacion sin lineas."})
        issue_date = attrs.get("issue_date", getattr(self.instance, "issue_date", None))
        expiry_date = attrs.get("expiry_date", getattr(self.instance, "expiry_date", None))
        if issue_date and expiry_date and issue_date > expiry_date:
            raise serializers.ValidationError({"expiry_date": ["La fecha de vencimiento debe ser igual o posterior a la fecha de emision."]})
        return attrs

    def _prepare_item(self, tenant, item_data):
        catalog_item = item_data.get("catalog_item")
        if catalog_item:
            if catalog_item.tenant_id != tenant.id:
                raise serializers.ValidationError("El item de catalogo no pertenece a tu tenant.")
            if not catalog_item.is_active or catalog_item.deleted_at:
                raise serializers.ValidationError("No puedes usar items de catalogo inactivos.")
            item_data.setdefault("description", catalog_item.description or catalog_item.name)
            item_data.setdefault("unit", catalog_item.unit)
            item_data.setdefault("unit_price", catalog_item.unit_price)
            item_data.setdefault("taxable", catalog_item.taxable)
        for field in ("description", "unit", "unit_price"):
            if field not in item_data or item_data[field] in ("", None):
                raise serializers.ValidationError({field: ["Este campo es obligatorio si no usas un item de catalogo."]})
        line_serializer = QuoteItemSerializer(data=item_data)
        line_serializer.is_valid(raise_exception=True)
        return line_serializer.validated_data

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        request = self.context["request"]
        tenant = request.user.tenant
        currency = validated_data.pop("currency", None) or tenant.default_currency
        quote = Quote.objects.create(
            tenant=tenant,
            created_by=request.user,
            quote_number=next_quote_number(tenant),
            tax_rate_snapshot=tenant.tax_rate,
            currency=currency,
            **validated_data,
        )
        for index, item_data in enumerate(items_data, start=1):
            item_data.setdefault("position", index)
            QuoteItem.objects.create(quote=quote, **self._prepare_item(tenant, item_data))
        recalculate_quote(quote)
        log_quote_activity(quote, "created", actor=request.user, message="Quote created.")
        return quote

    @transaction.atomic
    def update(self, instance, validated_data):
        if instance.status != Quote.Status.DRAFT:
            raise serializers.ValidationError("Only draft quotes can be edited.")
        items_data = validated_data.pop("items", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if items_data is not None:
            instance.items.all().delete()
            for index, item_data in enumerate(items_data, start=1):
                item_data.setdefault("position", index)
                QuoteItem.objects.create(quote=instance, **self._prepare_item(instance.tenant, item_data))
        recalculate_quote(instance)
        log_quote_activity(instance, "updated", actor=self.context["request"].user, message="Quote updated.")
        return instance


class QuoteActivityLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor.email", read_only=True)

    class Meta:
        model = QuoteActivityLog
        fields = ["id", "event", "message", "metadata", "actor", "actor_email", "created_at"]
        read_only_fields = fields


class PublicQuoteResponseSerializer(serializers.Serializer):
    response = serializers.ChoiceField(choices=["accepted", "rejected"])
    message = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def save(self, **kwargs):
        quote = self.context["quote"]
        request = self.context.get("request")
        if is_quote_expired(quote):
            validate_status_transition(quote.status, Quote.Status.EXPIRED)
            quote.status = Quote.Status.EXPIRED
            quote.save(update_fields=["status", "updated_at"])
            log_quote_activity(quote, "expired", message="Quote expired before public response.")
            log_security_event("quote_status_changed", request=request, tenant=quote.tenant, metadata={"quote": quote.quote_number, "status": "expired"})
            raise serializers.ValidationError({"detail": "Esta cotizacion ya vencio y no puede responderse."})
        if quote.status in {Quote.Status.ACCEPTED, Quote.Status.REJECTED}:
            raise serializers.ValidationError({"detail": "Esta cotizacion ya fue respondida."})
        if quote.status not in {Quote.Status.SENT, Quote.Status.VIEWED}:
            raise serializers.ValidationError({"detail": "Esta cotizacion aun no esta disponible para respuesta publica."})
        validate_status_transition(quote.status, self.validated_data["response"])
        quote.status = self.validated_data["response"]
        quote.client_responded_at = timezone.now()
        quote.save(update_fields=["status", "client_responded_at", "updated_at"])
        log_quote_activity(
            quote,
            f"client_{quote.status}",
            message=self.validated_data.get("message", ""),
        )
        log_security_event(
            "quote_public_responded",
            request=request,
            tenant=quote.tenant,
            metadata={"quote": quote.quote_number, "response": quote.status},
        )
        log_security_event(
            "quote_status_changed",
            request=request,
            tenant=quote.tenant,
            metadata={"quote": quote.quote_number, "status": quote.status},
        )
        return quote
