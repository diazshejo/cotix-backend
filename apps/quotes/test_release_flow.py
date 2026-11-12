from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from django.db import close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from apps.clients.models import Client
from apps.quotes.models import Quote, QuoteActivityLog, QuoteItem
from apps.quotes.services import recalculate_quote
from apps.tenants.models import Tenant
from apps.users.models import User


class QuoteReleaseFlowTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            slug="release-flow", business_name="Release Flow", email="owner@example.com",
            prices_include_tax=True, tax_rate=Decimal("12.00"),
        )
        self.user = User.objects.create_user(
            email="owner@example.com", password="StrongPass123", tenant=self.tenant,
            role=User.Role.TENANT_ADMIN, email_verified=True,
        )
        self.customer = Client.objects.create(tenant=self.tenant, name="Buyer", email="buyer@example.com")
        self.client.force_authenticate(self.user)

    def create_quote(self, *, currency="GTQ", status=Quote.Status.DRAFT):
        quote = Quote.objects.create(
            tenant=self.tenant, quote_number=f"Q-{Quote.objects.count() + 1}",
            client=self.customer, created_by=self.user, title="Service",
            issue_date=timezone.localdate(), currency=currency, status=status,
            tax_rate_snapshot=self.tenant.tax_rate,
            prices_include_tax_snapshot=self.tenant.prices_include_tax,
        )
        QuoteItem.objects.create(
            quote=quote, description="Visible service", quantity=Decimal("1"),
            unit="hour", unit_price=Decimal("100"), taxable=True,
        )
        recalculate_quote(quote)
        return quote

    def test_fractional_quantity_fixed_discount_and_inclusive_tax_snapshot(self):
        payload = {
            "client": str(self.customer.pk), "title": "Half hour",
            "issue_date": str(timezone.localdate()), "currency": "GTQ",
            "items": [{
                "description": "Half hour", "quantity": "0.5", "unit": "hour",
                "unit_price": "100", "discount_type": "fixed",
                "discount_value": "5", "taxable": True,
            }],
        }
        response = self.client.post("/api/quotes/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Decimal(response.data["subtotal"]), Decimal("50.00"))
        self.assertEqual(Decimal(response.data["discount_total"]), Decimal("5.00"))
        self.assertEqual(Decimal(response.data["taxable_base"]), Decimal("40.18"))
        self.assertEqual(Decimal(response.data["total_tax"]), Decimal("4.82"))
        self.assertEqual(Decimal(response.data["total"]), Decimal("45.00"))
        self.assertTrue(response.data["prices_include_tax_snapshot"])

        self.tenant.prices_include_tax = False
        self.tenant.tax_rate = Decimal("20.00")
        self.tenant.save(update_fields=["prices_include_tax", "tax_rate"])
        quote = Quote.objects.get(pk=response.data["id"])
        recalculate_quote(quote)
        self.assertEqual(quote.total, Decimal("45.00"))
        self.assertEqual(quote.tax_rate_snapshot, Decimal("12.00"))

    def test_reports_do_not_mix_currencies(self):
        self.create_quote(currency="GTQ")
        self.create_quote(currency="USD")
        gtq = self.client.get("/api/reports/dashboard/?currency=GTQ")
        usd = self.client.get("/api/reports/dashboard/?currency=USD")
        self.assertEqual(gtq.status_code, 200, gtq.data)
        self.assertEqual(usd.status_code, 200, usd.data)
        self.assertEqual(gtq.data["currency"], "GTQ")
        self.assertEqual(usd.data["currency"], "USD")
        self.assertEqual(Decimal(gtq.data["total_quoted"]), Decimal("100.00"))
        self.assertEqual(Decimal(usd.data["total_quoted"]), Decimal("100.00"))

    def test_public_pdf_works_without_login_and_omits_soft_deleted_line(self):
        quote = self.create_quote(status=Quote.Status.SENT)
        hidden = QuoteItem.objects.create(
            quote=quote, description="Hidden service", quantity=1,
            unit="hour", unit_price=999, taxable=True,
        )
        hidden.soft_delete()
        recalculate_quote(quote)
        self.client.force_authenticate(None)

        response = self.client.get(f"/api/public/quotes/{quote.public_token}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item["description"] for item in response.data["items"]], ["Visible service"])

        pdf = self.client.get(f"/api/public/quotes/{quote.public_token}/pdf/")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertNotIn(b"Hidden service", pdf.content)

    def test_draft_pdf_is_not_public(self):
        quote = self.create_quote()
        self.client.force_authenticate(None)
        response = self.client.get(f"/api/public/quotes/{quote.public_token}/pdf/")
        self.assertEqual(response.status_code, 403)

    def test_expired_response_commits_expired_state(self):
        quote = self.create_quote(status=Quote.Status.SENT)
        quote.expiry_date = timezone.localdate() - timezone.timedelta(days=1)
        quote.save(update_fields=["expiry_date"])
        self.client.force_authenticate(None)
        response = self.client.post(
            f"/api/public/quotes/{quote.public_token}/respond/",
            {"response": "accepted"}, format="json",
        )
        self.assertEqual(response.status_code, 400)
        quote.refresh_from_db()
        self.assertEqual(quote.status, Quote.Status.EXPIRED)


class PublicResponseConcurrencyTests(TransactionTestCase):
    def test_only_first_public_response_changes_quote(self):
        tenant = Tenant.objects.create(slug="public-race", business_name="Public Race", email="race@example.com")
        user = User.objects.create_user(email="race@example.com", password="StrongPass123", tenant=tenant)
        customer = Client.objects.create(tenant=tenant, name="Buyer", email="buyer@example.com")
        quote = Quote.objects.create(
            tenant=tenant, quote_number="RACE-1", client=customer,
            created_by=user, title="Race", issue_date=timezone.localdate(), status=Quote.Status.SENT,
        )
        QuoteItem.objects.create(quote=quote, description="Service", quantity=1, unit="hour", unit_price=100)
        recalculate_quote(quote)

        def respond(decision):
            close_old_connections()
            try:
                return APIClient().post(
                    f"/api/public/quotes/{quote.public_token}/respond/",
                    {"response": decision}, format="json",
                ).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(respond, ["accepted", "rejected"]))

        self.assertCountEqual(results, [200, 400])
        quote.refresh_from_db()
        self.assertIn(quote.status, {Quote.Status.ACCEPTED, Quote.Status.REJECTED})
        self.assertEqual(QuoteActivityLog.objects.filter(quote=quote, event__startswith="client_").count(), 1)
