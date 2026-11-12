import logging

import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def _price_id(plan: str, interval: str) -> str:
    return settings.STRIPE_PRICES.get((plan, interval), "")


def _price_to_plan_map() -> dict[str, str]:
    """Inverse of STRIPE_PRICES: price_id → plan name."""
    return {price_id: plan for (plan, _), price_id in settings.STRIPE_PRICES.items() if price_id}


class BillingService:

    @staticmethod
    def get_or_create_customer(tenant) -> str:
        if tenant.stripe_customer_id:
            return tenant.stripe_customer_id
        stripe.api_key = settings.STRIPE_SECRET_KEY
        customer = stripe.Customer.create(
            email=tenant.email,
            name=tenant.business_name,
            metadata={"tenant_id": str(tenant.id), "tenant_slug": tenant.slug},
        )
        tenant.stripe_customer_id = customer.id
        tenant.save(update_fields=["stripe_customer_id", "updated_at"])
        logger.info("billing_customer_created tenant_id=%s customer_id=%s", tenant.id, customer.id)
        return customer.id

    @staticmethod
    @transaction.atomic
    def create_checkout_session(tenant, plan: str, interval: str, success_url: str, cancel_url: str) -> str:
        from apps.tenants.models import Tenant

        stripe.api_key = settings.STRIPE_SECRET_KEY
        price_id = _price_id(plan, interval)
        if not price_id:
            raise ValueError(f"No hay un precio de Stripe configurado para plan={plan} intervalo={interval}.")

        # Serialize concurrent checkout attempts for the same tenant.
        tenant = Tenant.objects.select_for_update().get(pk=tenant.pk)
        customer_id = BillingService.get_or_create_customer(tenant)
        subscriptions = stripe.Subscription.list(customer=customer_id, limit=100)
        if any(sub.status not in {"canceled", "incomplete_expired"} for sub in subscriptions.auto_paging_iter()):
            raise ValueError("Ya tienes una suscripción. Gestiona el plan desde el portal de facturación.")

        sessions = stripe.checkout.Session.list(customer=customer_id, status="open", limit=100)
        for existing in sessions.auto_paging_iter():
            if existing.mode != "subscription":
                continue
            if existing.metadata.get("plan") == plan and existing.metadata.get("interval") == interval:
                return existing.url
            stripe.checkout.Session.expire(existing.id)

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            allow_promotion_codes=True,
            metadata={"tenant_id": str(tenant.id), "plan": plan, "interval": interval},
            subscription_data={"metadata": {"tenant_id": str(tenant.id)}},
        )
        logger.info("billing_checkout_created tenant_id=%s plan=%s interval=%s", tenant.id, plan, interval)
        return session.url

    @staticmethod
    def create_portal_session(tenant, return_url: str) -> str:
        stripe.api_key = settings.STRIPE_SECRET_KEY
        customer_id = BillingService.get_or_create_customer(tenant)
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return session.url

    @staticmethod
    def handle_webhook(payload: bytes, sig_header: str) -> None:
        from apps.tenants.models import StripeWebhookEvent

        stripe.api_key = settings.STRIPE_SECRET_KEY
        event = stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
        if event.type not in {
            "checkout.session.completed",
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        }:
            return
        if StripeWebhookEvent.objects.filter(pk=event.id).exists():
            return

        obj = event.data.object
        if event.type == "checkout.session.completed":
            if obj.mode != "subscription" or not obj.subscription:
                return
            subscription_id = obj.subscription
            checkout_tenant_id = obj.metadata.get("tenant_id")
        else:
            subscription_id = obj.id
            checkout_tenant_id = None

        # Read the current state: a stale webhook must not undo a newer change.
        subscription = stripe.Subscription.retrieve(subscription_id)
        metadata = subscription.get("metadata") or {}
        tenant_id = metadata.get("tenant_id") or checkout_tenant_id
        if not tenant_id:
            from apps.tenants.models import Tenant
            tenant_id = Tenant.objects.filter(stripe_customer_id=subscription.get("customer")).values_list("id", flat=True).first()
        if not tenant_id:
            logger.warning("stripe_webhook no tenant for subscription=%s", subscription_id)
            return

        with transaction.atomic():
            _, created = StripeWebhookEvent.objects.get_or_create(
                event_id=event.id, defaults={"event_type": event.type}
            )
            if not created:
                return
            BillingService._apply_subscription(tenant_id, subscription)

    @staticmethod
    def _apply_subscription(tenant_id: str, subscription) -> None:
        from apps.tenants.models import Tenant

        tenant = Tenant.objects.select_for_update().filter(pk=tenant_id).first()
        if tenant is None:
            logger.warning("stripe_webhook unknown tenant=%s", tenant_id)
            return
        customer_id = subscription.get("customer")
        if tenant.stripe_customer_id and tenant.stripe_customer_id != customer_id:
            logger.warning("stripe_webhook customer mismatch tenant_id=%s", tenant_id)
            return

        subscription_id = subscription.get("id")
        created_at = int(subscription.get("created") or 0)
        current_id = tenant.stripe_subscription_id
        if not current_id and created_at < tenant.stripe_subscription_created:
            return
        if current_id and current_id != subscription_id:
            if created_at <= tenant.stripe_subscription_created:
                return
            if subscription.get("status") not in {"active", "trialing"}:
                return

        if subscription.get("status") in {"active", "trialing"}:
            items = (subscription.get("items") or {}).get("data") or []
            price_id = (items[0].get("price") or {}).get("id") if items else None
            plan = _price_to_plan_map().get(price_id)
            if not plan:
                logger.warning("stripe_webhook unknown price_id=%s", price_id)
                return
            tenant.plan = plan
            tenant.status = "active"
            tenant.stripe_subscription_id = subscription_id
            tenant.stripe_subscription_created = created_at
            if not tenant.stripe_customer_id:
                tenant.stripe_customer_id = customer_id
            tenant.save(update_fields=["plan", "status", "stripe_subscription_id", "stripe_subscription_created", "stripe_customer_id", "updated_at"])
            logger.info("stripe_webhook tenant_id=%s activated plan=%s", tenant_id, plan)
        elif current_id == subscription_id:
            tenant.plan = "free"
            tenant.status = "demo"
            tenant.trial_ends_at = min(tenant.trial_ends_at, timezone.now())
            tenant.stripe_subscription_id = ""
            tenant.stripe_subscription_created = max(tenant.stripe_subscription_created, created_at)
            tenant.save(update_fields=["plan", "status", "trial_ends_at", "stripe_subscription_id", "stripe_subscription_created", "updated_at"])
            logger.info("stripe_webhook tenant_id=%s downgraded (sub status=%s)", tenant_id, subscription.get("status"))
