import logging
import os

import stripe
from django.conf import settings
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.audit import log_security_event
from apps.common.permissions import TenantSettingsPermission, TenantUserAdminPermission
from apps.tenants.models import Tenant
from apps.tenants.serializers import TenantSerializer
from apps.tenants.services.billing import BillingService

logger = logging.getLogger(__name__)

_LOGO_ALLOWED_TYPES = {"image/png", "image/jpeg"}
_LOGO_MAX_BYTES = 5 * 1024 * 1024


class LogoUploadView(APIView):
    permission_classes = [IsAuthenticated, TenantSettingsPermission]
    parser_classes = [MultiPartParser]

    def post(self, request):
        file = request.FILES.get("logo")
        if not file:
            return Response({"logo": ["No se proporcionó ningún archivo."]}, status=400)
        if file.content_type not in _LOGO_ALLOWED_TYPES:
            return Response({"logo": ["Solo se permiten archivos PNG o JPG."]}, status=400)
        if file.size > _LOGO_MAX_BYTES:
            return Response({"logo": ["El archivo no puede superar 5 MB."]}, status=400)

        if request.user.is_super_admin:
            tenant = Tenant.objects.filter(deleted_at__isnull=True).first()
        else:
            tenant = request.user.tenant

        ext = os.path.splitext(file.name)[1].lower() or ".png"
        rel_path = f"logos/{tenant.pk}{ext}"
        abs_path = os.path.join(settings.MEDIA_ROOT, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "wb+") as dst:
            for chunk in file.chunks():
                dst.write(chunk)

        logo_url = request.build_absolute_uri(settings.MEDIA_URL + rel_path)
        tenant.logo_url = logo_url
        tenant.save(update_fields=["logo_url", "updated_at"])
        log_security_event("tenant_logo_uploaded", request=request, tenant=tenant, metadata={"logo_url": logo_url})
        return Response({"logo_url": logo_url})


class TenantSettingsView(RetrieveUpdateAPIView):
    serializer_class = TenantSerializer
    permission_classes = [IsAuthenticated, TenantSettingsPermission]

    def get_object(self):
        if self.request.user.is_super_admin:
            return Tenant.objects.filter(deleted_at__isnull=True).first()
        return self.request.user.tenant

    def perform_update(self, serializer):
        tenant = serializer.save()
        log_security_event(
            "tenant_settings_updated",
            request=self.request,
            tenant=tenant,
            metadata={"fields": sorted(serializer.validated_data.keys())},
        )


class BillingCheckoutView(APIView):
    permission_classes = [IsAuthenticated, TenantUserAdminPermission]

    def post(self, request):
        plan = request.data.get("plan", "")
        interval = request.data.get("interval", "monthly")

        if plan not in ("basic", "premium", "business"):
            return Response({"error": "Plan invalido."}, status=400)
        if interval not in ("monthly", "annual"):
            return Response({"error": "Intervalo invalido."}, status=400)

        try:
            checkout_url = BillingService.create_checkout_session(
                tenant=request.user.tenant,
                plan=plan,
                interval=interval,
                success_url=f"{settings.FRONTEND_URL}/app/settings/billing?success=true",
                cancel_url=f"{settings.FRONTEND_URL}/app/settings/billing?cancelled=true",
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=400)
        except stripe.StripeError as exc:
            logger.error("billing_checkout_error tenant_id=%s error=%s", request.user.tenant_id, exc)
            return Response({"error": "No se pudo iniciar el pago. Intenta de nuevo."}, status=503)

        return Response({"checkout_url": checkout_url})


class BillingPortalView(APIView):
    permission_classes = [IsAuthenticated, TenantUserAdminPermission]

    def post(self, request):
        try:
            portal_url = BillingService.create_portal_session(
                tenant=request.user.tenant,
                return_url=f"{settings.FRONTEND_URL}/app/settings/billing",
            )
        except stripe.StripeError as exc:
            logger.error("billing_portal_error tenant_id=%s error=%s", request.user.tenant_id, exc)
            return Response({"error": "No se pudo abrir el portal de facturacion."}, status=503)

        return Response({"portal_url": portal_url})


class BillingWebhookView(APIView):
    """Recibe eventos de Stripe. Autenticado via firma STRIPE_WEBHOOK_SECRET, no via JWT."""
    permission_classes = []
    authentication_classes = []

    def post(self, request):
        sig_header = request.META.get("HTTP_STRIPE_SIGNATURE")
        if not sig_header:
            return Response({"error": "Missing Stripe-Signature header"}, status=400)

        try:
            BillingService.handle_webhook(request.body, sig_header)
        except (ValueError, stripe.SignatureVerificationError) as exc:
            logger.warning("stripe_webhook_invalid: %s", exc)
            return Response({"error": "Invalid payload or signature"}, status=400)

        return Response({"status": "ok"})
