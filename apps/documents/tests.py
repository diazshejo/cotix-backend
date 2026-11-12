from decimal import Decimal
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework.test import APIClient, APIRequestFactory

from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import CatalogItem
from apps.clients.models import Client
from apps.documents.services import generate_quote_pdf_response, generate_quote_reportlab_pdf, render_quote_html
from apps.quotes.models import Quote, QuoteItem
from apps.quotes.serializers import PublicQuoteSerializer
from apps.quotes.services import next_quote_number, recalculate_quote
from apps.tenants.models import Tenant
from apps.tenants.serializers import TenantSerializer
from apps.users.models import User


class QuotePdfTemplateTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            slug="tenant-pdf",
            business_name="Distribuidora Garcia S.A.",
            trade_name="Distribuidora Garcia",
            nit="1234567-8",
            email="ventas@example.com",
            phone="+502 5555-0101",
            address_line1="7a Avenida 3-45",
            city="Quetzaltenango",
            department="Quetzaltenango",
            primary_color="#0e7490",
            default_footer_text="Cotizacion valida por 15 dias calendario.",
        )
        self.user = User.objects.create_user(
            email="admin-pdf@example.com",
            password="StrongPass123",
            role=User.Role.TENANT_ADMIN,
            tenant=self.tenant,
        )
        self.client_obj = Client.objects.create(
            tenant=self.tenant,
            name="Roberto Lima",
            company_name="Constructora del Norte S.A.",
            email="cliente@example.com",
            phone="+502 5555-2222",
            nit="9876543-2",
            address_line1="4a Calle 12-30",
            city="Huehuetenango",
            department="Huehuetenango",
        )
        self.item = CatalogItem.objects.create(
            tenant=self.tenant,
            name="Cemento Portland",
            description="Cemento Portland tipo I",
            sku="CEM-001",
            unit_price=Decimal("98.0000"),
            unit="saco",
            currency="GTQ",
            taxable=True,
        )

    def make_quote(self):
        quote = Quote.objects.create(
            tenant=self.tenant,
            quote_number=next_quote_number(self.tenant),
            client=self.client_obj,
            created_by=self.user,
            title="Suministro de materiales para Proyecto Los Cipreses",
            issue_date=timezone.localdate(),
            expiry_date=timezone.localdate() + timezone.timedelta(days=15),
            currency="GTQ",
            tax_rate_snapshot=self.tenant.tax_rate,
            notes="Entrega estimada: 3-5 dias habiles.",
            terms_conditions="Precios sujetos a disponibilidad de inventario.",
        )
        QuoteItem.objects.create(
            quote=quote,
            catalog_item=self.item,
            description=self.item.description,
            quantity=Decimal("8.0000"),
            unit=self.item.unit,
            unit_price=self.item.unit_price,
            discount_type="percent",
            discount_value=Decimal("5.0000"),
            taxable=True,
        )
        recalculate_quote(quote)
        return quote

    def test_quote_pdf_html_uses_commercial_layout_sections(self):
        html = render_quote_html(self.make_quote())

        self.assertIn('class="header"', html)
        self.assertIn("Detalle de productos y servicios", html)
        self.assertIn("Constructora del Norte S.A.", html)
        self.assertIn("SKU: CEM-001", html)
        self.assertIn("Base imponible", html)
        self.assertIn("Generado con <strong>Cotix</strong>", html)
        self.assertNotIn("<table class=\"totals\">", html)
        self.assertIn("Cotizacion valida por 15 dias calendario.", html)

    def test_light_brand_uses_dark_text_in_pdf(self):
        self.tenant.primary_color = "#fff4bf"
        self.tenant.save(update_fields=["primary_color"])
        html = render_quote_html(self.make_quote())
        self.assertIn("--brand-foreground: #0f172a", html)
        self.assertIn("--brand-ink:    #0f172a", html)

    def test_footer_length_is_bounded_by_backend(self):
        serializer = TenantSerializer(self.tenant, data={"default_footer_text": "x" * 2001}, partial=True)
        self.assertFalse(serializer.is_valid())
        self.assertIn("default_footer_text", serializer.errors)

    def test_footer_text_is_escaped_in_pdf(self):
        self.tenant.default_footer_text = "<script>alert(1)</script>"
        self.tenant.save(update_fields=["default_footer_text"])
        html = render_quote_html(self.make_quote())
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_quote_pdf_response_still_returns_pdf(self):
        response = generate_quote_pdf_response(self.make_quote(), actor=self.user)

        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertGreater(len(response.content), 1000)

    def test_external_logo_is_never_embedded_or_fetched(self):
        quote = self.make_quote()
        self.tenant.logo_url = "http://127.0.0.1:12345/internal"
        self.tenant.save(update_fields=["logo_url"])
        html = render_quote_html(quote)
        self.assertNotIn("127.0.0.1:12345", html)
        request = APIRequestFactory().get("/api/public/quotes/example/")
        self.assertEqual(PublicQuoteSerializer(quote, context={"request": request}).data["company_logo_url"], "")
        response = generate_quote_pdf_response(quote, actor=self.user)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_logo_upload_reencodes_image_and_embeds_local_data(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            client = APIClient()
            client.force_authenticate(self.user)
            image = BytesIO()
            Image.new("RGB", (8, 8), "red").save(image, format="JPEG")
            upload = SimpleUploadedFile("logo.jpg", image.getvalue(), content_type="image/jpeg")
            response = client.post("/api/tenant/settings/logo/", {"logo": upload}, format="multipart")
            self.assertEqual(response.status_code, 200)
            self.tenant.refresh_from_db()
            self.assertTrue(self.tenant.logo_url.endswith(f"/media/logos/{self.tenant.pk}.png"))
            html = render_quote_html(self.make_quote())
            self.assertIn("data:image/png;base64,", html)
            self.assertNotIn(self.tenant.logo_url, html)
            request = APIRequestFactory().get("/api/public/quotes/example/")
            public_data = PublicQuoteSerializer(self.make_quote(), context={"request": request}).data
            self.assertEqual(public_data["company_logo_url"], f"http://testserver/media/logos/{self.tenant.pk}.png")
            self.assertEqual(public_data["company_footer_text"], self.tenant.default_footer_text)
            self.assertEqual(TenantSerializer(self.tenant, context={"request": request}).data["logo_url"], public_data["company_logo_url"])
            with override_settings(DEBUG=False):
                logo_response = client.get(f"/media/logos/{self.tenant.pk}.png")
            self.assertEqual(logo_response.status_code, 200)
            self.assertEqual(logo_response["Content-Type"], "image/png")
            self.assertTrue(b"".join(logo_response.streaming_content).startswith(b"\x89PNG"))
            self.assertIn(b"/Subtype /Image", generate_quote_reportlab_pdf(self.make_quote()))

    def test_public_logo_route_rejects_missing_or_foreign_files(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root, DEBUG=False):
            other = Tenant.objects.create(slug="other-pdf", business_name="Other", email="other@example.com")
            logo_dir = Path(media_root) / "logos"
            logo_dir.mkdir()
            Image.new("RGB", (8, 8), "red").save(logo_dir / f"{other.pk}.png", format="PNG")
            client = APIClient()
            self.assertEqual(client.get(f"/media/logos/{other.pk}.png").status_code, 404)
            self.assertEqual(client.get(f"/media/logos/{self.tenant.pk}.svg").status_code, 404)

    def test_legacy_local_jpeg_logo_remains_visible(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            logo_dir = Path(media_root) / "logos"
            logo_dir.mkdir()
            logo = logo_dir / f"{self.tenant.pk}.jpg"
            Image.new("RGB", (8, 8), "red").save(logo, format="JPEG")
            self.tenant.logo_url = f"http://localhost:8000/media/logos/{self.tenant.pk}.jpg"
            self.tenant.save(update_fields=["logo_url"])
            self.assertIn("data:image/png;base64,", render_quote_html(self.make_quote()))
