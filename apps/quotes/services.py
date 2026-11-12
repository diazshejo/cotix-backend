import hashlib
import json
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
    taxable_gross = Decimal("0.00")

    for item in quote.items.filter(deleted_at__isnull=True):
        line_subtotal, discount_amount, line_total = calculate_line(item)
        item.line_subtotal = line_subtotal
        item.discount_amount = discount_amount
        item.line_total = line_total
        item.save(update_fields=["line_subtotal", "discount_amount", "line_total", "updated_at"])
        subtotal += line_subtotal
        discount_total += discount_amount
        if item.taxable:
            if quote.prices_include_tax_snapshot:
                taxable_gross += line_total
            else:
                taxable_base += line_total

    if quote.prices_include_tax_snapshot:
        rate = quote.tax_rate_snapshot / Decimal("100")
        taxable_base = money(taxable_gross / (Decimal("1") + rate))
        total_tax = money(taxable_gross - taxable_base)
    else:
        total_tax = money(taxable_base * (quote.tax_rate_snapshot / Decimal("100")))
    quote.subtotal = money(subtotal)
    quote.discount_total = money(discount_total)
    quote.taxable_base = money(taxable_base)
    quote.total_tax = total_tax
    quote.total = money(quote.subtotal - quote.discount_total + (Decimal("0") if quote.prices_include_tax_snapshot else total_tax))
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
        "sent": {"viewed", "accepted", "rejected", "changes_requested", "expired"},
        "viewed": {"accepted", "rejected", "changes_requested", "expired"},
        "accepted": set(),
        "rejected": set(),
        "changes_requested": set(),
        "expired": set(),
    }
    if target not in allowed.get(current, set()):
        raise ValidationError({"detail": f"No puedes cambiar el estado de {current} a {target}."})


def is_quote_expired(quote):
    return bool(quote.expiry_date and quote.expiry_date < timezone.localdate())


def quote_acceptance_snapshot(quote):
    """Canonical contents accepted by the signer, independent of later contact edits."""
    snapshot = {
        "schema_version": 1,
        "quote_id": str(quote.pk),
        "quote_number": quote.quote_number,
        "tenant_id": str(quote.tenant_id),
        "tenant_name": quote.tenant.business_name,
        "client_id": str(quote.client_id),
        "client_name": quote.client.company_name or quote.client.name,
        "title": quote.title,
        "issue_date": quote.issue_date.isoformat(),
        "expiry_date": quote.expiry_date.isoformat() if quote.expiry_date else None,
        "currency": quote.currency,
        "notes": quote.notes,
        "terms_conditions": quote.terms_conditions,
        "subtotal": str(quote.subtotal),
        "discount_total": str(quote.discount_total),
        "taxable_base": str(quote.taxable_base),
        "tax_rate_snapshot": str(quote.tax_rate_snapshot),
        "prices_include_tax_snapshot": quote.prices_include_tax_snapshot,
        "total_tax": str(quote.total_tax),
        "total": str(quote.total),
        "items": [
            {
                "position": item.position,
                "description": item.description,
                "quantity": str(item.quantity),
                "unit": item.unit,
                "unit_price": str(item.unit_price),
                "discount_type": item.discount_type,
                "discount_value": str(item.discount_value),
                "discount_amount": str(item.discount_amount),
                "line_subtotal": str(item.line_subtotal),
                "line_total": str(item.line_total),
                "taxable": item.taxable,
            }
            for item in quote.items.filter(deleted_at__isnull=True).order_by("position", "created_at")
        ],
    }
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return snapshot, digest


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
    """Count all quotes created this month, including deleted drafts, for plan quotas."""
    now = timezone.now()
    return Quote.objects.filter(
        tenant=tenant,
        created_at__year=now.year,
        created_at__month=now.month,
    ).count()
