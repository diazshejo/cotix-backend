from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import CatalogItem
from apps.clients.models import Client
from apps.documents.services import generate_quote_pdf_response, render_quote_html
from apps.quotes.models import Quote, QuoteItem
from apps.quotes.services import next_quote_number, recalculate_quote
from apps.tenants.models import Tenant
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

    def test_quote_pdf_response_still_returns_pdf(self):
        response = generate_quote_pdf_response(self.make_quote(), actor=self.user)

        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertGreater(len(response.content), 1000)
