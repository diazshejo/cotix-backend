import logging

import stripe
from django.conf import settings

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
    def create_checkout_session(tenant, plan: str, interval: str, success_url: str, cancel_url: str) -> str:
        stripe.api_key = settings.STRIPE_SECRET_KEY
        price_id = _price_id(plan, interval)
        if not price_id:
            raise ValueError(f"No hay un precio de Stripe configurado para plan={plan} intervalo={interval}.")

        customer_id = BillingService.get_or_create_customer(tenant)
        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            allow_promotion_codes=True,
            metadata={"tenant_id": str(tenant.id)},
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
        stripe.api_key = settings.STRIPE_SECRET_KEY
        event = stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
        logger.info("stripe_webhook event_type=%s id=%s", event.type, event.id)

        if event.type == "checkout.session.completed":
            session = event.data.object
            if session.mode == "subscription" and session.subscription:
                BillingService._on_checkout_completed(session)

        elif event.type in ("customer.subscription.updated", "customer.subscription.deleted"):
            BillingService._on_subscription_changed(event.data.object)

    @staticmethod
    def _on_checkout_completed(session) -> None:
        stripe.api_key = settings.STRIPE_SECRET_KEY
        tenant_id = session.metadata.get("tenant_id")
        if not tenant_id:
            logger.warning("stripe_webhook checkout.session.completed: missing tenant_id in metadata")
            return
        subscription = stripe.Subscription.retrieve(session.subscription)
        BillingService._apply_subscription(tenant_id, subscription)

    @staticmethod
    def _on_subscription_changed(subscription) -> None:
        tenant_id = subscription.metadata.get("tenant_id")
        if not tenant_id:
            from apps.tenants.models import Tenant
            try:
                tenant = Tenant.objects.get(stripe_customer_id=subscription.customer)
                tenant_id = str(tenant.id)
            except Tenant.DoesNotExist:
                logger.warning("stripe_webhook sub changed: no tenant for customer=%s", subscription.customer)
                return
        BillingService._apply_subscription(tenant_id, subscription)

    @staticmethod
    def _apply_subscription(tenant_id: str, subscription) -> None:
        from apps.tenants.models import Tenant
        price_id = subscription.items.data[0].price.id
        plan = _price_to_plan_map().get(price_id)

        if not plan:
            logger.warning("stripe_webhook unknown price_id=%s — no plan mapped", price_id)
            return

        if subscription.status in ("active", "trialing"):
            Tenant.objects.filter(id=tenant_id).update(
                plan=plan,
                status="active",
                stripe_subscription_id=subscription.id,
            )
            logger.info("stripe_webhook tenant_id=%s activated plan=%s", tenant_id, plan)
        else:
            Tenant.objects.filter(id=tenant_id).update(
                plan="free",
                status="demo",
                stripe_subscription_id="",
            )
            logger.info("stripe_webhook tenant_id=%s downgraded (sub status=%s)", tenant_id, subscription.status)
