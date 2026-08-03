from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.quotes.models import Quote, QuoteActivityLog


MONEY = Decimal("0.01")


def money(value):
    return Decimal(value or 0).quantize(MONEY, rounding=ROUND_HALF_UP)


def calculate_line(item):
    line_subtotal = money(item.quantity * item.unit_price)
    if item.discount_type == "percent":
        discount_amount = money(line_subtotal * (item.discount_value / Decimal("100")))
    elif item.discount_type == "fixed":
        discount_amount = money(item.discount_value)
    else:
        discount_amount = Decimal("0.00")
    discount_amount = min(discount_amount, line_subtotal)
    line_total = money(line_subtotal - discount_amount)
    return line_subtotal, discount_amount, line_total


def recalculate_quote(quote):
    subtotal = Decimal("0.00")
    discount_total = Decimal("0.00")
    taxable_base = Decimal("0.00")

    for item in quote.items.filter(deleted_at__isnull=True):
        line_subtotal, discount_amount, line_total = calculate_line(item)
        item.line_subtotal = line_subtotal
        item.discount_amount = discount_amount
        item.line_total = line_total
        item.save(update_fields=["line_subtotal", "discount_amount", "line_total", "updated_at"])
        subtotal += line_subtotal
        discount_total += discount_amount
        if item.taxable:
            taxable_base += line_total

    total_tax = money(taxable_base * (quote.tax_rate_snapshot / Decimal("100")))
    quote.subtotal = money(subtotal)
    quote.discount_total = money(discount_total)
    quote.taxable_base = money(taxable_base)
    quote.total_tax = total_tax
    quote.total = money(quote.subtotal - quote.discount_total + total_tax)
    quote.save(
        update_fields=[
            "subtotal",
            "discount_total",
            "taxable_base",
            "total_tax",
            "total",
            "updated_at",
        ]
    )
    return quote


def validate_quote_ready(quote, *, require_email=False):
    if not quote.client_id:
        raise ValidationError({"detail": "La cotizacion debe tener cliente."})
    if not quote.client.is_active or quote.client.deleted_at:
        raise ValidationError({"detail": "No puedes usar un cliente inactivo en una cotizacion."})
    if not quote.items.filter(deleted_at__isnull=True).exists():
        raise ValidationError({"detail": "No puedes enviar una cotizacion sin lineas."})
    if quote.total <= 0:
        raise ValidationError({"detail": "No puedes enviar una cotizacion con total menor o igual a cero."})
    if require_email and not quote.client.email:
        raise ValidationError({"detail": "No puedes enviar por email una cotizacion si el cliente no tiene email."})


def validate_status_transition(current, target):
    if current == target:
        return
    allowed = {
        "draft": {"sent", "expired"},
        "sent": {"viewed", "accepted", "rejected", "expired"},
        "viewed": {"accepted", "rejected", "expired"},
        "accepted": set(),
        "rejected": set(),
        "expired": set(),
    }
    if target not in allowed.get(current, set()):
        raise ValidationError({"detail": f"No puedes cambiar el estado de {current} a {target}."})


def is_quote_expired(quote):
    return bool(quote.expiry_date and quote.expiry_date < timezone.localdate())


@transaction.atomic
def next_quote_number(tenant):
    locked_tenant = type(tenant).objects.select_for_update().get(pk=tenant.pk)
    digits = getattr(locked_tenant, "quote_digits", 4)
    number = f"{locked_tenant.quote_prefix}-{timezone.now().year}-{locked_tenant.quote_next_number:0{digits}d}"
    locked_tenant.quote_next_number += 1
    locked_tenant.save(update_fields=["quote_next_number", "updated_at"])
    return number


def log_quote_activity(quote, event, actor=None, message="", metadata=None):
    return QuoteActivityLog.objects.create(
        quote=quote,
        actor=actor,
        event=event,
        message=message,
        metadata=metadata or {},
    )


def count_monthly_quotes(tenant) -> int:
    """Cuenta cotizaciones no eliminadas creadas en el mes calendario actual."""
    now = timezone.now()
    return Quote.objects.filter(
        tenant=tenant,
        deleted_at__isnull=True,
        created_at__year=now.year,
        created_at__month=now.month,
    ).count()
