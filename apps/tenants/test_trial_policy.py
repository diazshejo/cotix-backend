from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from apps.clients.models import Client
from apps.quotes.models import Quote, QuoteItem
from apps.quotes.services import recalculate_quote
from apps.tenants.models import Tenant
from apps.tenants.services.plans import PlanService, TRIAL_EXPIRED_MESSAGE
from apps.users.models import User


class TrialPolicyTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            slug="trial-policy", business_name="Trial Policy", email="trial@example.com"
        )
        self.user = User.objects.create_user(
            email="trial@example.com", password="StrongPass123", tenant=self.tenant,
            role=User.Role.TENANT_ADMIN, email_verified=True,
        )
        self.customer = Client.objects.create(
            tenant=self.tenant, name="Buyer", email="buyer@example.com"
        )
        self.client.force_authenticate(self.user)

    def quote_payload(self):
        return {
            "client": str(self.customer.pk), "title": "Service",
            "issue_date": str(timezone.localdate()),
            "items": [{"description": "Service", "quantity": "1", "unit": "hour", "unit_price": "100"}],
        }

    def create_quote(self):
        quote = Quote.objects.create(
            tenant=self.tenant, quote_number=f"TRIAL-{Quote.objects.count() + 1}",
            client=self.customer, created_by=self.user, title="Service",
            issue_date=timezone.localdate(),
        )
        QuoteItem.objects.create(
            quote=quote, description="Service", quantity=1, unit="hour", unit_price=Decimal("100")
        )
        recalculate_quote(quote)
        return quote

    def expire_trial(self):
        self.tenant.trial_ends_at = timezone.now() - timedelta(seconds=1)
        self.tenant.save(update_fields=["trial_ends_at"])

    def test_new_tenant_has_30_days_and_basic_limits(self):
        self.assertAlmostEqual(
            (self.tenant.trial_ends_at - timezone.now()).total_seconds(),
            30 * 24 * 60 * 60, delta=10,
        )
        limits = PlanService.get_limits(self.tenant)
        self.assertEqual(limits.monthly_quotes, 50)
        self.assertEqual(limits.max_users, 1)
        response = self.client.get("/api/auth/me/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["tenant"]["trial_expired"])
        self.assertIsNotNone(response.data["tenant"]["trial_ends_at"])

    def test_free_trial_does_not_allow_a_second_user(self):
        response = self.client.post(
            "/api/auth/users/",
            {"email": "other@example.com", "password": "StrongPass123", "role": User.Role.SELLER},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("1", response.data["detail"])

    def test_expired_trial_blocks_business_writes_but_keeps_data_readable(self):
        quote = self.create_quote()
        self.expire_trial()
        self.assertEqual(self.client.get(f"/api/quotes/{quote.pk}/").status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me/").data["tenant"]["trial_expired"], True)

        attempts = [
            self.client.post("/api/quotes/", self.quote_payload(), format="json"),
            self.client.post(f"/api/quotes/{quote.pk}/duplicate/", {}, format="json"),
            self.client.post("/api/clients/", {"name": "Another"}, format="json"),
            self.client.post(
                "/api/auth/users/",
                {"email": "other@example.com", "password": "StrongPass123", "role": User.Role.SELLER},
                format="json",
            ),
            self.client.patch("/api/tenant/settings/", {"business_name": "Changed"}, format="json"),
        ]
        for response in attempts:
            self.assertEqual(response.status_code, 403, response.data)
            self.assertEqual(response.data["detail"], TRIAL_EXPIRED_MESSAGE)
        self.assertEqual(Quote.objects.filter(tenant=self.tenant).count(), 1)

    @patch("apps.tenants.views.BillingService.create_checkout_session", return_value="https://checkout.example/pay")
    def test_expired_trial_can_still_open_checkout(self, checkout):
        self.expire_trial()
        response = self.client.post(
            "/api/tenant/billing/checkout/", {"plan": "basic", "interval": "monthly"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["checkout_url"], "https://checkout.example/pay")
        checkout.assert_called_once()

    def test_paid_plan_can_write_after_original_trial_end(self):
        self.expire_trial()
        self.tenant.plan = "basic"
        self.tenant.status = "active"
        self.tenant.save(update_fields=["plan", "status"])
        response = self.client.post("/api/quotes/", self.quote_payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)

    def test_duplicate_counts_toward_monthly_limit(self):
        source = self.create_quote()
        for index in range(49):
            Quote.objects.create(
                tenant=self.tenant, quote_number=f"EXTRA-{index}", client=self.customer,
                created_by=self.user, title="Other", issue_date=timezone.localdate(),
            )
        response = self.client.post(f"/api/quotes/{source.pk}/duplicate/", {}, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(Quote.objects.filter(tenant=self.tenant).count(), 50)

    def test_duplicate_allowed_when_one_slot_remains(self):
        source = self.create_quote()
        for index in range(48):
            Quote.objects.create(
                tenant=self.tenant, quote_number=f"EXTRA-{index}", client=self.customer,
                created_by=self.user, title="Other", issue_date=timezone.localdate(),
            )
        response = self.client.post(f"/api/quotes/{source.pk}/duplicate/", {}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Quote.objects.filter(tenant=self.tenant).count(), 50)

    def test_deleting_a_draft_does_not_restore_monthly_quota(self):
        source = self.create_quote()
        for index in range(49):
            Quote.objects.create(
                tenant=self.tenant, quote_number=f"EXTRA-{index}", client=self.customer,
                created_by=self.user, title="Other", issue_date=timezone.localdate(),
            )
        source.soft_delete()
        response = self.client.post("/api/quotes/", self.quote_payload(), format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(Quote.objects.filter(tenant=self.tenant).count(), 50)


class TrialQuotaConcurrencyTests(TransactionTestCase):
    def test_two_creates_compete_for_one_remaining_quote(self):
        tenant = Tenant.objects.create(slug="trial-concurrent", business_name="Trial Concurrent", email="race@example.com")
        user = User.objects.create_user(
            email="race@example.com", password="StrongPass123", tenant=tenant,
            role=User.Role.TENANT_ADMIN, email_verified=True,
        )
        customer = Client.objects.create(tenant=tenant, name="Buyer", email="buyer@example.com")
        Quote.objects.bulk_create([
            Quote(
                tenant=tenant, quote_number=f"PRE-{index}", client=customer,
                created_by=user, title="Earlier", issue_date=timezone.localdate(),
            )
            for index in range(49)
        ])
        payload = {
            "client": str(customer.pk), "title": "New",
            "issue_date": str(timezone.localdate()),
            "items": [{"description": "Service", "quantity": "1", "unit": "hour", "unit_price": "100"}],
        }

        def create(_):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(user)
                return client.post("/api/quotes/", payload, format="json").status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(create, range(2)))

        self.assertCountEqual(results, [201, 403])
        self.assertEqual(Quote.objects.filter(tenant=tenant).count(), 50)
