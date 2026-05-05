from django.conf import settings
from django.core.mail import EmailMessage
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.common.audit import log_security_event
from apps.quotes.services import log_quote_activity, validate_quote_ready, validate_status_transition


def render_quote_html(quote):
    return render_to_string("documents/quote_pdf.html", {"quote": quote})


def generate_quote_pdf_response(quote, actor=None):
    validate_quote_ready(quote)
    if not quote.tenant.business_name or not quote.tenant.email:
        raise ValidationError({"detail": "No puedes generar PDF sin nombre y email del tenant."})
    if not quote.client.name:
        raise ValidationError({"detail": "No puedes generar PDF sin nombre del cliente."})
    html = render_quote_html(quote)
    try:
        from weasyprint import HTML

        pdf = HTML(string=html).write_pdf()
        content = pdf
    except Exception:
        content = generate_quote_reportlab_pdf(quote)

    quote.pdf_generated_at = timezone.now()
    quote.save(update_fields=["pdf_generated_at", "updated_at"])
    log_quote_activity(quote, "pdf_generated", actor=actor, message="PDF generated synchronously.")

    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{quote.quote_number}.pdf"'
    return response


def generate_quote_reportlab_pdf(quote):
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(quote.tenant.trade_name or quote.tenant.business_name, styles["Title"]),
        Paragraph(f"NIT: {quote.tenant.nit or 'CF'} | {quote.tenant.email}", styles["Normal"]),
        Spacer(1, 14),
        Paragraph(f"Cotizacion {quote.quote_number}", styles["Heading2"]),
        Paragraph(f"Estado: {quote.status} | Emision: {quote.issue_date} | Vence: {quote.expiry_date or '-'}", styles["Normal"]),
        Spacer(1, 12),
        Paragraph("Cliente", styles["Heading3"]),
        Paragraph(f"{quote.client.company_name or quote.client.name} | NIT: {quote.client.nit or 'CF'}", styles["Normal"]),
        Spacer(1, 12),
    ]
    rows = [["#", "Descripcion", "Cant.", "Unidad", "Precio", "Desc.", "Total"]]
    for item in quote.items.filter(deleted_at__isnull=True):
        rows.append([
            item.position,
            item.description,
            item.quantity,
            item.unit,
            item.unit_price,
            item.discount_amount,
            item.line_total,
        ])
    table = Table(rows, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.extend([table, Spacer(1, 12)])
    totals = Table([
        ["Subtotal", quote.subtotal],
        ["Descuentos", quote.discount_total],
        [f"{quote.tenant.tax_label} {quote.tax_rate_snapshot}%", quote.total_tax],
        [f"Total {quote.currency}", quote.total],
    ], hAlign="RIGHT")
    totals.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey)]))
    story.append(totals)
    if quote.notes:
        story.extend([Spacer(1, 12), Paragraph("Notas", styles["Heading3"]), Paragraph(quote.notes, styles["Normal"])])
    if quote.terms_conditions:
        story.extend([Spacer(1, 12), Paragraph("Terminos y condiciones", styles["Heading3"]), Paragraph(quote.terms_conditions, styles["Normal"])])
    doc.build(story)
    return buffer.getvalue()


def send_quote_email(quote, to_email, subject=None, body=None, actor=None):
    validate_quote_ready(quote, require_email=True)
    if not subject and not quote.quote_number:
        raise ValidationError({"detail": "No puedes enviar email sin numero de cotizacion."})
    if subject is not None and not subject.strip():
        raise ValidationError({"subject": ["El asunto no puede estar vacio."]})
    if body is not None and not body.strip():
        raise ValidationError({"body": ["El cuerpo del email no puede estar vacio."]})
    public_url = f"{settings.FRONTEND_URL}/q/{quote.public_token}"
    subject = subject or f"Cotizacion {quote.quote_number}"
    body = body or (
        f"Hola {quote.client.name},\n\n"
        f"Te compartimos la cotizacion {quote.quote_number} por {quote.currency} {quote.total}.\n"
        f"Puedes verla aqui: {public_url}\n"
    )
    message = EmailMessage(subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=[to_email])
    try:
        message.send(fail_silently=False)
    except Exception:
        log_quote_activity(quote, "email_failed", actor=actor, message="No se pudo enviar el email.", metadata={"to": to_email})
        log_security_event(
            "quote_email_failed",
            tenant=quote.tenant,
            user=actor,
            severity="warning",
            metadata={"quote": quote.quote_number, "to": to_email},
        )
        raise ValidationError({"detail": "No se pudo enviar el email. Revisa la configuracion de correo e intenta de nuevo."})
    if quote.status == "draft":
        validate_status_transition(quote.status, "sent")
        quote.status = "sent"
        quote.save(update_fields=["status", "updated_at"])
        log_security_event(
            "quote_status_changed",
            tenant=quote.tenant,
            user=actor,
            metadata={"quote": quote.quote_number, "status": "sent"},
        )
    log_quote_activity(
        quote,
        "sent_email",
        actor=actor,
        message=f"Quote sent by email to {to_email}.",
        metadata={"to": to_email},
    )
    log_security_event(
        "quote_email_sent",
        tenant=quote.tenant,
        user=actor,
        metadata={"quote": quote.quote_number, "to": to_email},
    )
    return {"to": to_email, "status": "sent"}
