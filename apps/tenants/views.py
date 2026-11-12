import logging
from io import BytesIO
from pathlib import Path

import stripe
from django.conf import settings
from django.http import FileResponse, Http404
from django.views.decorators.http import require_safe
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.audit import log_security_event
from apps.common.permissions import TenantBillingPermission, TenantSettingsPermission
from apps.tenants.models import Tenant
from apps.tenants.branding import tenant_logo_path
from apps.tenants.serializers import TenantSerializer
from apps.tenants.services.billing import BillingService

logger = logging.getLogger(__name__)

_LOGO_ALLOWED_TYPES = {"image/png", "image/jpeg"}
_LOGO_MAX_BYTES = 5 * 1024 * 1024


@require_safe
def public_tenant_logo(request, tenant_id, extension):
    if extension not in {"png", "jpg", "jpeg"}:
        raise Http404
    tenant = Tenant.objects.filter(pk=tenant_id, deleted_at__isnull=True).first()
    if tenant is None or tenant_logo_path(tenant) != f"{settings.MEDIA_URL}logos/{tenant_id}.{extension}":
        raise Http404
    logo = Path(settings.MEDIA_ROOT) / "logos" / f"{tenant_id}.{extension}"
    try:
        if logo.stat().st_size > _LOGO_MAX_BYTES:
            raise Http404
        response = FileResponse(logo.open("rb"), content_type="image/png" if extension == "png" else "image/jpeg")
    except FileNotFoundError as exc:
        raise Http404 from exc
    response["Cache-Control"] = "public, no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    return response


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

        if tenant is None:
            return Response({"logo": ["No se encontró la empresa."]}, status=404)
        try:
            image = Image.open(file)
            if image.format not in {"PNG", "JPEG"} or image.width > 4096 or image.height > 4096:
                raise ValueError("Formato o tamaño de imagen no permitido.")
            image = ImageOps.exif_transpose(image)
            image.thumbnail((1024, 1024))
            mode = "RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB"
            output = BytesIO()
            image.convert(mode).save(output, format="PNG")
            if output.tell() > _LOGO_MAX_BYTES:
                raise ValueError("La imagen procesada supera el tamaño permitido.")
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
            return Response({"logo": ["El archivo no es una imagen PNG o JPG válida de hasta 4096 píxeles."]}, status=400)
        rel_path = f"logos/{tenant.pk}.png"
        abs_path = Path(settings.MEDIA_ROOT) / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(output.getvalue())

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
    permission_classes = [IsAuthenticated, TenantBillingPermission]

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
    permission_classes = [IsAuthenticated, TenantBillingPermission]

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
