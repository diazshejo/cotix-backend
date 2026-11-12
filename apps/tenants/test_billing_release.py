from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.tenants.models import StripeWebhookEvent, Tenant
from apps.tenants.services.billing import BillingService
from apps.tenants.services.plans import PlanService


def subscription(sub_id, *, created=100, status="active", price="price_basic"):
    # A Stripe subscription is dict-like; `subscription.items` is a method.
    return {
        "id": sub_id, "customer": "cus_release", "created": created,
        "status": status, "metadata": {"tenant_id": "tenant-id"},
        "items": {"data": [{"price": {"id": price}}]},
    }


@override_settings(STRIPE_PRICES={("basic", "monthly"): "price_basic", ("premium", "monthly"): "price_premium"})
class BillingReleaseTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            slug="billing-release", business_name="Billing Release",
            email="billing@example.com", stripe_customer_id="cus_release",
        )

    def test_webhook_applies_current_subscription_only_once(self):
        current = subscription("sub_current", price="price_premium")
        current["metadata"]["tenant_id"] = str(self.tenant.pk)
        event = SimpleNamespace(
            id="evt_current", type="customer.subscription.updated",
            data=SimpleNamespace(object=SimpleNamespace(id="sub_current")),
        )
        with patch("stripe.Webhook.construct_event", return_value=event), patch(
            "stripe.Subscription.retrieve", return_value=current
        ) as retrieve:
            BillingService.handle_webhook(b"payload", "signature")
            BillingService.handle_webhook(b"payload", "signature")

        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.plan, "premium")
        self.assertEqual(self.tenant.status, "active")
        self.assertEqual(self.tenant.stripe_subscription_id, "sub_current")
        self.assertEqual(StripeWebhookEvent.objects.count(), 1)
        self.assertEqual(retrieve.call_count, 1)

    def test_old_subscription_event_cannot_replace_newer_one(self):
        self.tenant.plan = "premium"
        self.tenant.status = "active"
        self.tenant.stripe_subscription_id = "sub_new"
        self.tenant.stripe_subscription_created = 200
        self.tenant.save()
        BillingService._apply_subscription(str(self.tenant.pk), subscription("sub_old", created=100))
        BillingService._apply_subscription(str(self.tenant.pk), subscription("sub_old", created=100, status="canceled"))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.plan, "premium")
        self.assertEqual(self.tenant.stripe_subscription_id, "sub_new")

    def test_cancelled_subscription_keeps_ordering_watermark(self):
        self.tenant.plan = "basic"
        self.tenant.status = "active"
        self.tenant.stripe_subscription_id = "sub_current"
        self.tenant.stripe_subscription_created = 200
        self.tenant.save()
        BillingService._apply_subscription(
            str(self.tenant.pk), subscription("sub_current", created=200, status="canceled")
        )
        BillingService._apply_subscription(str(self.tenant.pk), subscription("sub_old", created=100))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.plan, "free")
        self.assertEqual(self.tenant.stripe_subscription_id, "")
        self.assertEqual(self.tenant.stripe_subscription_created, 200)
        self.assertLessEqual(self.tenant.trial_ends_at, timezone.now())
        self.assertTrue(PlanService.trial_expired(self.tenant))

        BillingService._apply_subscription(str(self.tenant.pk), subscription("sub_new", created=201))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.plan, "basic")
        self.assertFalse(PlanService.trial_expired(self.tenant))

    def test_existing_subscription_prevents_duplicate_checkout(self):
        entries = SimpleNamespace(auto_paging_iter=lambda: iter([SimpleNamespace(status="active")]))
        with patch("stripe.Subscription.list", return_value=entries), patch("stripe.checkout.Session.create") as create:
            with self.assertRaisesRegex(ValueError, "Ya tienes una suscripción"):
                BillingService.create_checkout_session(self.tenant, "basic", "monthly", "success", "cancel")
        create.assert_not_called()

    def test_matching_open_checkout_is_reused(self):
        subscriptions = SimpleNamespace(auto_paging_iter=lambda: iter([]))
        checkout = SimpleNamespace(
            auto_paging_iter=lambda: iter([SimpleNamespace(
                mode="subscription", metadata={"plan": "basic", "interval": "monthly"},
                url="https://checkout.example/session",
            )])
        )
        with patch("stripe.Subscription.list", return_value=subscriptions), patch(
            "stripe.checkout.Session.list", return_value=checkout
        ), patch("stripe.checkout.Session.create") as create:
            url = BillingService.create_checkout_session(self.tenant, "basic", "monthly", "success", "cancel")
        self.assertEqual(url, "https://checkout.example/session")
        create.assert_not_called()
