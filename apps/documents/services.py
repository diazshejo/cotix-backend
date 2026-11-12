from base64 import b64encode
from html import escape
from io import BytesIO
from pathlib import Path
import re

from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError
from django.core.mail import EmailMessage
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.common.audit import log_security_event
from apps.quotes.services import log_quote_activity, validate_quote_ready, validate_status_transition
from apps.tenants.branding import tenant_logo_path


def _brand_rgba(hex_color: str, alpha: float) -> str:
    hex_color = hex_color.lstrip("#")
    try:
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"
    except (ValueError, IndexError):
        return f"rgba(26,111,219,{alpha})"


def _brand_luminance(hex_color: str) -> float:
    channels = [int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _brand_foreground(hex_color: str) -> str:
    luminance = _brand_luminance(hex_color)
    return "#0f172a" if (luminance + 0.05) / 0.059 > 1.05 / (luminance + 0.05) else "#ffffff"


def _brand_ink(hex_color: str) -> str:
    return hex_color if 1.05 / (_brand_luminance(hex_color) + 0.05) >= 4.5 else "#0f172a"


def _logo_bytes(tenant):
    path = tenant_logo_path(tenant)
    if not path:
        return b""
    logo = Path(settings.MEDIA_ROOT) / "logos" / Path(path).name
    try:
        if logo.stat().st_size > 5 * 1024 * 1024:
            return b""
        with Image.open(logo) as image:
            if image.format not in {"PNG", "JPEG"} or image.width > 4096 or image.height > 4096:
                return b""
            image = ImageOps.exif_transpose(image)
            image.thumbnail((1024, 1024))
            mode = "RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB"
            output = BytesIO()
            image.convert(mode).save(output, format="PNG")
        if output.tell() > 5 * 1024 * 1024:
            return b""
        return output.getvalue()
    except (FileNotFoundError, UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        return b""


def _logo_data_uri(tenant):
    logo = _logo_bytes(tenant)
    return "data:image/png;base64," + b64encode(logo).decode("ascii") if logo else ""


def render_quote_html(quote):
    brand = (getattr(quote.tenant, "primary_color", None) or "#1a6fdb").strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", brand):
        brand = "#1a6fdb"
    acceptance = getattr(quote, "acceptance", None)
    signature_lines = []
    if acceptance:
        signature_lines = [
            " ".join(f"{point['x'] * 1000:.1f},{point['y'] * 300:.1f}" for point in stroke)
            for stroke in acceptance.signature_strokes
        ]
    return render_to_string("documents/quote_pdf.html", {
        "logo_data_uri": _logo_data_uri(quote.tenant),
        "quote": quote,
        "items": quote.items.filter(deleted_at__isnull=True).select_related("catalog_item"),
        "brand_color": brand,
        "brand_foreground": _brand_foreground(brand),
        "brand_ink": _brand_ink(brand),
        "brand_color_light": _brand_rgba(brand, 0.08),
        "brand_color_border": _brand_rgba(brand, 0.22),
        "acceptance": acceptance,
        "signature_lines": signature_lines,
    })


def generate_quote_pdf_response(quote, actor=None):
    validate_quote_ready(quote)
    if not quote.tenant.business_name or not quote.tenant.email:
        raise ValidationError({"detail": "No puedes generar PDF sin nombre y email del tenant."})
    if not quote.client.name:
        raise ValidationError({"detail": "No puedes generar PDF sin nombre del cliente."})
    html = render_quote_html(quote)
    try:
        from weasyprint import HTML
        from weasyprint.urls import URLFetcher

        # Templates may contain user text; never fetch network or local files.
        pdf = HTML(string=html, url_fetcher=URLFetcher(allowed_protocols={"data"}, allow_redirects=False)).write_pdf()
        content = pdf
    except ImportError:
        import logging
        logging.getLogger(__name__).warning("WeasyPrint not available, falling back to ReportLab.")
        content = generate_quote_reportlab_pdf(quote)
    except OSError as exc:
        import logging
        logging.getLogger(__name__).warning("WeasyPrint native libraries missing (%s), falling back to ReportLab.", exc)
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
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image as PdfImage, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.graphics.shapes import Drawing, PolyLine

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=14 * mm, leftMargin=14 * mm, topMargin=14 * mm, bottomMargin=16 * mm)
    styles = getSampleStyleSheet()
    brand = _reportlab_color(getattr(quote.tenant, "primary_color", "") or "#1a6fdb")
    brand_fg = colors.HexColor(_brand_foreground(brand.hexval().replace("0x", "#")))
    brand_ink = colors.HexColor(_brand_ink(brand.hexval().replace("0x", "#")))
    ink = colors.HexColor("#0f172a")
    muted = colors.HexColor("#64748b")
    soft = colors.HexColor("#f8fafc")
    line = colors.HexColor("#e2e8f0")
    danger = colors.HexColor("#dc2626")

    styles.add(ParagraphStyle("PdfCompany", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=17, leading=20, textColor=brand_fg))
    styles.add(ParagraphStyle("PdfHeaderSmall", parent=styles["Normal"], fontSize=8.5, leading=13, textColor=brand_fg))
    styles.add(ParagraphStyle("PdfQuoteNumber", parent=styles["Normal"], alignment=TA_RIGHT, fontName="Helvetica-Bold", fontSize=22, leading=25, textColor=brand_fg))
    styles.add(ParagraphStyle("PdfEyebrow", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=muted, uppercase=True))
    styles.add(ParagraphStyle("PdfPartyName", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=12.5, leading=16, textColor=ink))
    styles.add(ParagraphStyle("PdfBody", parent=styles["Normal"], fontSize=9.2, leading=13.5, textColor=colors.HexColor("#475569")))
    styles.add(ParagraphStyle("PdfSubject", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=13.5, leading=17, textColor=ink))
    styles.add(ParagraphStyle("PdfItem", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8.8, leading=12.5, textColor=ink))
    styles.add(ParagraphStyle("PdfSmallMuted", parent=styles["Normal"], fontSize=8, leading=11, textColor=colors.HexColor("#94a3b8")))
    styles.add(ParagraphStyle("PdfRight", parent=styles["Normal"], alignment=TA_RIGHT, fontSize=8.6, leading=11, textColor=ink))
    styles.add(ParagraphStyle("PdfCenter", parent=styles["Normal"], alignment=TA_CENTER, fontSize=8.6, leading=11, textColor=ink))

    company_lines = [
        f"{quote.tenant.business_name} | NIT: {quote.tenant.nit or 'CF'}",
        " ".join(part for part in [quote.tenant.address_line1, quote.tenant.city, quote.tenant.department] if part),
        " | ".join(part for part in [quote.tenant.email, quote.tenant.phone] if part),
    ]
    company_lines = [line_text for line_text in company_lines if line_text.strip(" |")]
    company_heading = Paragraph(escape(quote.tenant.trade_name or quote.tenant.business_name), styles["PdfCompany"])
    logo_bytes = _logo_bytes(quote.tenant)
    if logo_bytes:
        with Image.open(BytesIO(logo_bytes)) as logo_image:
            scale = min(15 * mm / logo_image.width, 15 * mm / logo_image.height)
            logo = PdfImage(BytesIO(logo_bytes), width=logo_image.width * scale, height=logo_image.height * scale)
        company_heading = Table([[logo, company_heading]], colWidths=[18 * mm, 88 * mm], style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
    header = Table(
        [
            [
                company_heading,
                Paragraph("COTIZACION", _style(styles, "PdfHeaderSmall", alignment=TA_RIGHT)),
            ],
            [
                Paragraph("<br/>".join(map(escape, company_lines)), styles["PdfHeaderSmall"]),
                Paragraph(f"#{quote.quote_number}", styles["PdfQuoteNumber"]),
            ],
            [
                Paragraph(f"Emision<br/><b>{quote.issue_date:%d/%m/%Y}</b>", styles["PdfHeaderSmall"]),
                Paragraph(f"Vence<br/><b>{quote.expiry_date.strftime('%d/%m/%Y') if quote.expiry_date else '-'}</b>", _style(styles, "PdfHeaderSmall", alignment=TA_RIGHT)),
            ],
        ],
        colWidths=[118 * mm, 50 * mm],
    )
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), brand),
        ("BOX", (0, 0), (-1, -1), 0, brand),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ("TOPPADDING", (0, 0), (-1, 0), 18),
        ("BOTTOMPADDING", (0, 2), (-1, 2), 16),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))

    client_lines = [
        f"Contacto: {quote.client.name}" if quote.client.company_name else "",
        " ".join(part for part in [quote.client.address_line1, quote.client.city, quote.client.department] if part),
        " | ".join(part for part in [quote.client.email, quote.client.phone] if part),
        f"NIT: {quote.client.nit or 'CF'}",
    ]
    tenant_lines = [
        quote.tenant.business_name,
        " ".join(part for part in [quote.tenant.address_line1, quote.tenant.city, quote.tenant.department] if part),
        " | ".join(part for part in [quote.tenant.email, quote.tenant.phone] if part),
        f"NIT: {quote.tenant.nit or 'CF'}",
    ]
    parties = Table(
        [
            [Paragraph("PARA CLIENTE", _style(styles, "PdfEyebrow", textColor=brand_ink)), Paragraph("DE EMPRESA", _style(styles, "PdfEyebrow", textColor=brand_ink))],
            [Paragraph(escape(quote.client.company_name or quote.client.name), styles["PdfPartyName"]), Paragraph(escape(quote.tenant.trade_name or quote.tenant.business_name), styles["PdfPartyName"])],
            [Paragraph("<br/>".join(map(escape, filter(None, client_lines))), styles["PdfBody"]), Paragraph("<br/>".join(map(escape, filter(None, tenant_lines))), styles["PdfBody"])],
        ],
        colWidths=[82 * mm, 82 * mm],
    )
    parties.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    story = [
        header,
        Spacer(1, 16),
        parties,
        Spacer(1, 12),
        Paragraph("ASUNTO / PROYECTO", styles["PdfEyebrow"]),
        Paragraph(escape(quote.title), styles["PdfSubject"]),
        Spacer(1, 16),
        Paragraph("DETALLE DE PRODUCTOS Y SERVICIOS", styles["PdfEyebrow"]),
        Spacer(1, 5),
    ]

    rows = [["#", "Descripcion", "Cant.", "Unidad", "P. unitario", "Desc.", "Subtotal"]]
    for item in quote.items.filter(deleted_at__isnull=True):
        description = Paragraph(escape(str(item.description)), styles["PdfItem"])
        if item.catalog_item_id and getattr(item.catalog_item, "sku", ""):
            description = Paragraph(f"{escape(item.description)}<br/><font color='#94a3b8' size='7'>SKU: {escape(item.catalog_item.sku)}</font>", styles["PdfItem"])
        rows.append([
            Paragraph(str(item.position), styles["PdfCenter"]),
            description,
            Paragraph(f"{item.quantity:.2f}", styles["PdfCenter"]),
            Paragraph(escape(item.unit), styles["PdfCenter"]),
            Paragraph(f"{quote.currency} {item.unit_price:.2f}", styles["PdfRight"]),
            Paragraph("-" if not item.discount_amount else f"{quote.currency} {item.discount_amount:.2f}", _style(styles, "PdfRight", textColor=danger)),
            Paragraph(f"{quote.currency} {item.line_total:.2f}", _style(styles, "PdfRight", fontName="Helvetica-Bold")),
        ])
    table = Table(rows, repeatRows=1, colWidths=[9 * mm, 62 * mm, 17 * mm, 18 * mm, 24 * mm, 20 * mm, 26 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), soft),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#7c8aa0")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7.6),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, line),
        ("LINEABOVE", (0, 0), (-1, 0), 0.75, line),
        ("LINEBELOW", (0, 1), (-1, -1), 0.35, colors.HexColor("#edf2f7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.extend([table, Spacer(1, 16)])

    note_parts = []
    if quote.notes:
        note_parts.extend([Paragraph("NOTAS PARA EL CLIENTE", styles["PdfEyebrow"]), Paragraph(escape(quote.notes), styles["PdfBody"]), Spacer(1, 8)])
    if quote.terms_conditions:
        note_parts.extend([Paragraph("TERMINOS Y CONDICIONES", styles["PdfEyebrow"]), Paragraph(escape(quote.terms_conditions), styles["PdfBody"])])
    if not note_parts:
        note_parts = [Paragraph("NOTAS", styles["PdfEyebrow"]), Paragraph("Sin notas adicionales.", styles["PdfBody"])]

    totals = Table([
        ["Subtotal", f"{quote.currency} {quote.subtotal:.2f}"],
        ["Descuentos", f"-{quote.currency} {quote.discount_total:.2f}"],
        ["Base imponible", f"{quote.currency} {quote.taxable_base:.2f}"],
        [f"{quote.tenant.tax_label} {quote.tax_rate_snapshot:.2f}%", f"{quote.currency} {quote.total_tax:.2f}"],
        ["TOTAL", f"{quote.currency} {quote.total:.2f}"],
    ], colWidths=[33 * mm, 32 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -2), soft),
        ("BOX", (0, 0), (-1, -1), 0.75, line),
        ("LINEBELOW", (0, 0), (-1, -2), 0.35, line),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -2), 8.8),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("TEXTCOLOR", (0, 0), (-1, -2), colors.HexColor("#475569")),
        ("TEXTCOLOR", (1, 1), (1, 1), danger),
        ("BACKGROUND", (0, -1), (-1, -1), brand),
        ("TEXTCOLOR", (0, -1), (-1, -1), brand_fg),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, -1), (0, -1), 9),
        ("FONTSIZE", (1, -1), (1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))

    summary = Table([[note_parts, totals]], colWidths=[96 * mm, 68 * mm])
    summary.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 12),
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
    ]))
    story.append(KeepTogether(summary))

    acceptance = getattr(quote, "acceptance", None)
    if acceptance:
        drawing = Drawing(420, 120)
        for stroke in acceptance.signature_strokes:
            coordinates = []
            for point in stroke:
                coordinates.extend([point["x"] * 420, (1 - point["y"]) * 120])
            drawing.add(PolyLine(coordinates, strokeColor=ink, strokeWidth=1.8))
        story.extend([
            Spacer(1, 16),
            Paragraph("ACEPTACIÓN CON FIRMA ELECTRÓNICA", styles["PdfEyebrow"]),
            drawing,
            Paragraph(escape(acceptance.signer_name), styles["PdfBody"]),
            Paragraph(f"Fecha: {acceptance.signed_at:%d/%m/%Y %H:%M %Z}", styles["PdfBody"]),
            Paragraph(escape(acceptance.consent_text), styles["PdfBody"]),
            Paragraph(f"Huella SHA-256 de la propuesta: {acceptance.snapshot_sha256}", styles["PdfSmallMuted"]),
        ])

    footer_text = quote.tenant.default_footer_text or "Documento generado con Cotix."
    story.extend([Spacer(1, 14), Paragraph(escape(footer_text), styles["PdfSmallMuted"])])
    doc.build(story, onFirstPage=_draw_page_footer(quote), onLaterPages=_draw_page_footer(quote))
    return buffer.getvalue()


def _reportlab_color(value):
    from reportlab.lib import colors

    try:
        return colors.HexColor(value)
    except Exception:
        return colors.HexColor("#1a6fdb")


def _style(styles, name, **overrides):
    from reportlab.lib.styles import ParagraphStyle

    base = styles[name]
    return ParagraphStyle(f"{name}_{abs(hash(str(overrides)))}", parent=base, **overrides)


def _draw_page_footer(quote):
    def draw(canvas, doc):
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm

        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#94a3b8"))
        canvas.drawString(doc.leftMargin, 8 * mm, quote.tenant.trade_name or quote.tenant.business_name)
        canvas.drawRightString(A4[0] - doc.rightMargin, 8 * mm, f"Pagina {doc.page} | {quote.quote_number}")
        canvas.restoreState()

    return draw


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
