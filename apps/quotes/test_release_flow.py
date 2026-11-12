from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from django.db import close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from apps.clients.models import Client
from apps.quotes.models import Quote, QuoteAcceptance, QuoteActivityLog, QuoteItem
from apps.quotes.services import recalculate_quote
from apps.documents.services import render_quote_html
from apps.notifications.models import Notification
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

    def test_request_changes_requires_detail_and_preserves_original_for_revision(self):
        quote = self.create_quote(status=Quote.Status.SENT)
        self.client.force_authenticate(None)
        url = f"/api/public/quotes/{quote.public_token}/respond/"
        self.assertEqual(self.client.post(url, {"response": "changes_requested", "message": "short"}, format="json").status_code, 400)
        response = self.client.post(url, {"response": "changes_requested", "message": "Necesito dos horas adicionales de servicio."}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        quote.refresh_from_db()
        self.assertEqual(quote.status, Quote.Status.CHANGES_REQUESTED)
        self.assertIn("dos horas", quote.change_request_message)
        self.assertTrue(Notification.objects.filter(quote=quote, notification_type="quote_changes_requested").exists())
        self.assertEqual(self.client.post(url, {"response": "rejected"}, format="json").status_code, 400)
        self.client.force_authenticate(self.user)
        revision = self.client.post(f"/api/quotes/{quote.pk}/duplicate/")
        self.assertEqual(revision.status_code, 201, revision.data)
        self.assertEqual(revision.data["status"], Quote.Status.DRAFT)
        self.assertEqual(str(revision.data["revision_of"]), str(quote.pk))
        self.assertNotEqual(revision.data["public_token"], quote.public_token)
        self.assertEqual(Quote.objects.get(pk=quote.pk).total, Quote.objects.get(pk=revision.data["id"]).total)

    def test_acceptance_requires_valid_signature_and_records_evidence(self):
        quote = self.create_quote(status=Quote.Status.SENT)
        self.client.force_authenticate(None)
        url = f"/api/public/quotes/{quote.public_token}/respond/"
        self.assertEqual(self.client.post(url, {"response": "accepted"}, format="json").status_code, 400)
        invalid = {"response": "accepted", "signer_name": "Buyer Name", "consent": True,
                   "signature_strokes": [[{"x": 2, "y": 0.5}] * 8]}
        self.assertEqual(self.client.post(url, invalid, format="json").status_code, 400)
        self.assertFalse(QuoteAcceptance.objects.filter(quote=quote).exists())
        signed = {"response": "accepted", "signer_name": "Buyer Name", "consent": True,
                  "signature_strokes": [[{"x": index / 10, "y": (index % 3) / 3} for index in range(10)]]}
        response = self.client.post(url, signed, format="json", HTTP_USER_AGENT="Cotix test")
        self.assertEqual(response.status_code, 200, response.data)
        evidence = QuoteAcceptance.objects.get(quote=quote)
        self.assertEqual(evidence.signer_name, "Buyer Name")
        self.assertEqual(evidence.quote_snapshot["total"], str(quote.total))
        self.assertEqual(len(evidence.snapshot_sha256), 64)
        self.assertIn(evidence.snapshot_sha256, render_quote_html(quote))
        public = self.client.get(f"/api/public/quotes/{quote.public_token}/")
        self.assertEqual(public.data["acceptance"]["signer_name"], "Buyer Name")
        self.assertEqual(public.data["consent_text"], evidence.consent_text)
        self.assertNotIn("signature_strokes", public.data["acceptance"])
        self.assertNotIn("connection_ip", str(public.data))
        pdf = self.client.get(f"/api/public/quotes/{quote.public_token}/pdf/")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.client.force_authenticate(self.user)
        internal = self.client.get(f"/api/quotes/{quote.pk}/")
        self.assertEqual(internal.status_code, 200)
        self.assertEqual(internal.data["acceptance"]["signature_strokes"], evidence.signature_strokes)
        self.assertEqual(self.client.post(url, signed, format="json").status_code, 400)

    def test_public_response_requires_csrf_token_in_browser(self):
        quote = self.create_quote(status=Quote.Status.SENT)
        browser = APIClient(enforce_csrf_checks=True)
        url = f"/api/public/quotes/{quote.public_token}/respond/"
        self.assertEqual(browser.post(url, {"response": "rejected"}, format="json").status_code, 403)
        token = browser.get("/api/auth/csrf/").data["csrf_token"]
        response = browser.post(url, {"response": "rejected"}, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200, response.data)


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
                    {"response": decision, **({"signer_name": "Buyer Name", "consent": True,
                        "signature_strokes": [[{"x": index / 10, "y": (index % 3) / 3} for index in range(10)]]}
                        if decision == "accepted" else {})}, format="json",
                ).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(respond, ["accepted", "rejected"]))

        self.assertCountEqual(results, [200, 400])
        quote.refresh_from_db()
        self.assertIn(quote.status, {Quote.Status.ACCEPTED, Quote.Status.REJECTED})
        self.assertEqual(QuoteActivityLog.objects.filter(quote=quote, event__startswith="client_").count(), 1)
