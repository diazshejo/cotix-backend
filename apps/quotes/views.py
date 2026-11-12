import datetime

from django.db import transaction
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.pagination import PageNumberPagination
from django.shortcuts import get_object_or_404
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from apps.common.permissions import TenantRolePermission
from apps.common.audit import log_security_event
from apps.common.throttles import PublicQuoteRespondThrottle, PublicQuoteViewThrottle, SendEmailThrottle
from apps.common.viewsets import TenantScopedQuerysetMixin
from apps.documents.services import generate_quote_pdf_response, send_quote_email
from apps.quotes.models import Quote, QuoteItem
from apps.quotes.serializers import (
    PublicQuoteResponseSerializer,
    QuoteActivityLogSerializer,
    QuoteItemSerializer,
    QuoteListSerializer,
    QuoteSerializer,
)
from apps.quotes.services import (
    count_monthly_quotes,
    is_quote_expired,
    log_quote_activity,
    next_quote_number,
    recalculate_quote,
    validate_quote_ready,
    validate_status_transition,
)
from apps.tenants.services.plans import QUOTA_MONTHLY_QUOTES, PlanService
from apps.tenants.models import Tenant


class PlanUsageView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = getattr(request.user, "tenant", None)
        if not tenant or request.user.is_super_admin:
            return Response({"monthly_quotes": {"used": 0, "limit": None}})
        limits = PlanService.get_limits(tenant)
        used = count_monthly_quotes(tenant)
        return Response({
            "monthly_quotes": {
                "used": used,
                "limit": limits.monthly_quotes,
            },
        })


class QuotePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class QuoteViewSet(TenantScopedQuerysetMixin, viewsets.ModelViewSet):
    queryset = (
        Quote.objects
        .select_related("tenant", "client", "created_by", "assigned_to")
        .prefetch_related("items__catalog_item")
    )
    serializer_class = QuoteSerializer
    permission_classes = [IsAuthenticated, TenantRolePermission]
    pagination_class = QuotePagination
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["quote_number", "title", "client__name", "client__email"]
    ordering_fields = ["created_at", "updated_at", "issue_date", "expiry_date", "total", "status", "quote_number"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return QuoteListSerializer
        return QuoteSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params

        # Filtro por status: ?status=draft  o  ?status=draft,sent
        status_param = params.get("status")
        if status_param:
            statuses = [s.strip() for s in status_param.split(",") if s.strip()]
            if statuses:
                qs = qs.filter(status__in=statuses)

        # Rango de fecha de emisión: ?issue_from=YYYY-MM-DD  ?issue_to=YYYY-MM-DD
        issue_from = params.get("issue_from")
        if issue_from:
            qs = qs.filter(issue_date__gte=issue_from)

        issue_to = params.get("issue_to")
        if issue_to:
            qs = qs.filter(issue_date__lte=issue_to)

        # Rango de vencimiento: ?expiry_from=YYYY-MM-DD  ?expiry_to=YYYY-MM-DD
        expiry_from = params.get("expiry_from")
        if expiry_from:
            qs = qs.filter(expiry_date__gte=expiry_from)

        expiry_to = params.get("expiry_to")
        if expiry_to:
            qs = qs.filter(expiry_date__lte=expiry_to)

        # Filtro por cliente: ?client=<uuid>
        client_id = params.get("client")
        if client_id:
            qs = qs.filter(client_id=client_id)

        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        tenant = getattr(self.request.user, "tenant", None)
        if tenant and not self.request.user.is_super_admin:
            tenant = Tenant.objects.select_for_update().get(pk=tenant.pk)
            PlanService.require_active_trial(tenant)
            PlanService.require_quota(tenant, QUOTA_MONTHLY_QUOTES, count_monthly_quotes(tenant))
        serializer.save()

    def perform_destroy(self, instance):
        if instance.status != Quote.Status.DRAFT:
            raise ValidationError({"detail": "Solo puedes eliminar cotizaciones en borrador."})
        instance.soft_delete()
        log_quote_activity(instance, "deleted", actor=self.request.user, message="Quote deleted.")

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def duplicate(self, request, pk=None):
        source = self.get_object()
        if not request.user.is_super_admin:
            tenant = Tenant.objects.select_for_update().get(pk=source.tenant_id)
            PlanService.require_active_trial(tenant)
            PlanService.require_quota(tenant, QUOTA_MONTHLY_QUOTES, count_monthly_quotes(tenant))
        quote = Quote.objects.create(
            tenant=source.tenant,
            quote_number=next_quote_number(source.tenant),
            client=source.client,
            created_by=request.user,
            assigned_to=source.assigned_to,
            title=f"Copy of {source.title}",
            issue_date=timezone.localdate(),
            expiry_date=source.expiry_date,
            currency=source.currency,
            internal_notes=source.internal_notes,
            notes=source.notes,
            terms_conditions=source.terms_conditions,
            tax_rate_snapshot=source.tax_rate_snapshot,
            prices_include_tax_snapshot=source.prices_include_tax_snapshot,
        )
        for item in source.items.filter(deleted_at__isnull=True):
            quote.items.create(
                catalog_item=item.catalog_item,
                position=item.position,
                description=item.description,
                quantity=item.quantity,
                unit=item.unit,
                unit_price=item.unit_price,
                discount_type=item.discount_type,
                discount_value=item.discount_value,
                taxable=item.taxable,
            )
        recalculate_quote(quote)
        log_quote_activity(quote, "duplicated", actor=request.user, message=f"Duplicated from {source.quote_number}.")
        return Response(self.get_serializer(quote).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="send-email", throttle_classes=[SendEmailThrottle])
    def send_email(self, request, pk=None):
        quote = self.get_object()
        to_email = request.data.get("to") or quote.client.email
        validate_quote_ready(quote, require_email=True)
        if to_email != quote.client.email:
            from django.core.validators import validate_email
            from django.core.exceptions import ValidationError as DjangoValidationError
            try:
                validate_email(to_email)
            except DjangoValidationError:
                raise ValidationError({"to": ["El email de destino no es valido."]})
        result = send_quote_email(
            quote,
            to_email=to_email,
            subject=request.data.get("subject"),
            body=request.data.get("body"),
            actor=request.user,
        )
        return Response(result)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def publish(self, request, pk=None):
        quote = self.get_queryset().select_for_update(of=("self",)).get(pk=self.get_object().pk)
        validate_quote_ready(quote)
        if is_quote_expired(quote):
            raise ValidationError({"detail": "No puedes compartir una cotización vencida."})
        validate_status_transition(quote.status, Quote.Status.SENT)
        if quote.status == Quote.Status.DRAFT:
            quote.status = Quote.Status.SENT
            quote.save(update_fields=["status", "updated_at"])
            log_quote_activity(quote, "published", actor=request.user, message="Quote shared by public link.")
            log_security_event("quote_status_changed", request=request, tenant=quote.tenant, metadata={"quote": quote.quote_number, "status": "sent"})
        return Response(self.get_serializer(quote).data)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        quote = self.get_object()
        validate_quote_ready(quote)
        return generate_quote_pdf_response(quote, actor=request.user)

    @action(detail=True, methods=["get"])
    def activity(self, request, pk=None):
        quote = self.get_object()
        serializer = QuoteActivityLogSerializer(quote.activity.all(), many=True)
        return Response(serializer.data)


class QuoteItemViewSet(viewsets.ModelViewSet):
    """
    CRUD granular de line items para una cotización en borrador.

    Endpoints:
      GET    /api/quotes/{quote_pk}/items/          → lista de items
      POST   /api/quotes/{quote_pk}/items/          → agregar item
      GET    /api/quotes/{quote_pk}/items/{pk}/     → detalle de item
      PATCH  /api/quotes/{quote_pk}/items/{pk}/     → actualizar item (parcial)
      DELETE /api/quotes/{quote_pk}/items/{pk}/     → eliminar item
    """
    serializer_class = QuoteItemSerializer
    permission_classes = [IsAuthenticated, TenantRolePermission]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def _get_quote(self) -> Quote:
        user = self.request.user
        qs = Quote.objects.filter(deleted_at__isnull=True)
        if not user.is_super_admin:
            if not user.tenant_id:
                raise PermissionDenied("El usuario no pertenece a un tenant.")
            qs = qs.filter(tenant=user.tenant)
        return get_object_or_404(qs, pk=self.kwargs["quote_pk"])

    def get_queryset(self):
        quote = self._get_quote()
        return (
            QuoteItem.objects
            .filter(quote=quote, deleted_at__isnull=True)
            .select_related("catalog_item")
            .order_by("position", "created_at")
        )

    def _require_draft(self, quote: Quote) -> None:
        if quote.status != Quote.Status.DRAFT:
            raise ValidationError({"detail": "Solo puedes modificar items de cotizaciones en borrador."})

    def _validate_catalog_item(self, catalog_item, quote: Quote) -> None:
        if catalog_item is None:
            return
        if catalog_item.tenant_id != quote.tenant_id:
            raise ValidationError({"catalog_item": ["El item de catalogo no pertenece a tu tenant."]})
        if not catalog_item.is_active or catalog_item.deleted_at:
            raise ValidationError({"catalog_item": ["No puedes usar items de catalogo inactivos."]})

    def _apply_catalog_defaults(self, validated_data: dict, quote: Quote) -> dict:
        """Rellena campos desde catalog_item y valida que los obligatorios estén presentes."""
        catalog_item = validated_data.get("catalog_item")
        extra: dict = {}
        if catalog_item:
            self._validate_catalog_item(catalog_item, quote)
            if not validated_data.get("description"):
                extra["description"] = catalog_item.description or catalog_item.name
            if not validated_data.get("unit"):
                extra["unit"] = catalog_item.unit
            if validated_data.get("unit_price") is None:
                extra["unit_price"] = catalog_item.unit_price
            if "taxable" not in validated_data:
                extra["taxable"] = catalog_item.taxable

        for field in ("description", "unit", "unit_price"):
            if not validated_data.get(field) and not extra.get(field):
                raise ValidationError({field: [f"'{field}' es obligatorio cuando no usas un item de catalogo."]})

        return extra

    def _next_position(self, quote: Quote) -> int:
        last = (
            QuoteItem.objects
            .filter(quote=quote, deleted_at__isnull=True)
            .values_list("position", flat=True)
            .order_by("-position")
            .first()
        )
        return (last or 0) + 1

    @transaction.atomic
    def perform_create(self, serializer):
        quote = self._get_quote()
        self._require_draft(quote)
        extra = self._apply_catalog_defaults(serializer.validated_data, quote)
        serializer.save(quote=quote, position=self._next_position(quote), **extra)
        recalculate_quote(quote)

    @transaction.atomic
    def perform_update(self, serializer):
        quote = serializer.instance.quote
        self._require_draft(quote)
        if "catalog_item" in serializer.validated_data:
            self._validate_catalog_item(serializer.validated_data["catalog_item"], quote)
        serializer.save()
        recalculate_quote(quote)

    @transaction.atomic
    def perform_destroy(self, instance):
        quote = instance.quote
        self._require_draft(quote)
        instance.soft_delete()
        recalculate_quote(quote)


class PublicQuoteView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PublicQuoteViewThrottle]

    def _get_quote(self, token):
        return get_object_or_404(
            Quote.objects.select_for_update(of=("self",)).select_related("tenant", "client").prefetch_related("items"),
            public_token=token,
            deleted_at__isnull=True,
        )

    @extend_schema(responses=QuoteSerializer)
    @transaction.atomic
    def get(self, request, token):
        quote = self._get_quote(token)
        if quote.status == Quote.Status.DRAFT:
            raise PermissionDenied("Esta cotización aún no está disponible para el cliente.")
        if is_quote_expired(quote) and quote.status in {Quote.Status.DRAFT, Quote.Status.SENT, Quote.Status.VIEWED}:
            validate_status_transition(quote.status, Quote.Status.EXPIRED)
            quote.status = Quote.Status.EXPIRED
            quote.save(update_fields=["status", "updated_at"])
            log_quote_activity(quote, "expired", message="Quote expired before public view.")
            log_security_event("quote_status_changed", request=request, tenant=quote.tenant, metadata={"quote": quote.quote_number, "status": "expired"})
        if quote.status == Quote.Status.SENT and not quote.client_first_viewed_at:
            validate_status_transition(quote.status, Quote.Status.VIEWED)
            quote.status = Quote.Status.VIEWED
            quote.client_first_viewed_at = timezone.now()
            quote.save(update_fields=["status", "client_first_viewed_at", "updated_at"])
            log_quote_activity(quote, "viewed", message="Client viewed public quote.")
            log_security_event("quote_public_viewed", request=request, tenant=quote.tenant, metadata={"quote": quote.quote_number})
            log_security_event("quote_status_changed", request=request, tenant=quote.tenant, metadata={"quote": quote.quote_number, "status": "viewed"})
        data = QuoteSerializer(quote, context={"request": request}).data
        data.pop("internal_notes", None)
        return Response(data)


class PublicQuoteRespondView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PublicQuoteRespondThrottle]

    @extend_schema(request=PublicQuoteResponseSerializer, responses=QuoteSerializer)
    @transaction.atomic
    def post(self, request, token):
        quote = get_object_or_404(
            Quote.objects.select_for_update().select_related("tenant", "client"),
            public_token=token, deleted_at__isnull=True,
        )
        serializer = PublicQuoteResponseSerializer(data=request.data, context={"quote": quote, "request": request})
        serializer.is_valid(raise_exception=True)
        try:
            serializer.save()
        except ValidationError as exc:
            if quote.status == Quote.Status.EXPIRED:
                return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
            raise
        return Response({"status": quote.status})


class PublicQuotePdfView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PublicQuoteViewThrottle]

    def get(self, request, token):
        quote = get_object_or_404(
            Quote.objects.select_related("tenant", "client"),
            public_token=token, deleted_at__isnull=True,
        )
        if quote.status == Quote.Status.DRAFT:
            raise PermissionDenied("Esta cotización aún no está disponible para el cliente.")
        return generate_quote_pdf_response(quote)


class DashboardReportView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_queryset(self, request):
        qs = Quote.objects.filter(deleted_at__isnull=True)
        if request.user.is_super_admin:
            return qs
        return qs.filter(tenant=request.user.tenant)

    def get(self, request):
        currency = request.query_params.get("currency", "GTQ")
        if currency not in {"GTQ", "USD"}:
            raise ValidationError({"currency": ["Moneda no válida."]})
        period = request.query_params.get("period", "month")
        if period not in {"week", "month", "quarter", "year"}:
            raise ValidationError({"period": ["Periodo no válido."]})
        today = timezone.localdate()
        starts = {
            "week": today - datetime.timedelta(days=today.weekday()),
            "month": today.replace(day=1),
            "quarter": today.replace(month=((today.month - 1) // 3) * 3 + 1, day=1),
            "year": today.replace(month=1, day=1),
        }
        queryset = self._get_queryset(request).filter(currency=currency, issue_date__range=(starts[period], today))
        total_quoted = queryset.aggregate(value=Sum("total"))["value"] or 0
        total_accepted = queryset.filter(status=Quote.Status.ACCEPTED).aggregate(value=Sum("total"))["value"] or 0
        sent_statuses = [Quote.Status.SENT, Quote.Status.VIEWED, Quote.Status.ACCEPTED, Quote.Status.REJECTED]
        quotes_sent = queryset.filter(status__in=sent_statuses).count()
        quotes_accepted = queryset.filter(status=Quote.Status.ACCEPTED).count()
        conversion_rate = round((quotes_accepted / quotes_sent) * 100, 2) if quotes_sent else 0
        recent_quotes = queryset.select_related("client").order_by("-created_at")[:5]
        expiring_quotes = (
            queryset.select_related("client")
            .filter(
                status__in=[Quote.Status.DRAFT, Quote.Status.SENT, Quote.Status.VIEWED],
                expiry_date__gte=timezone.localdate(),
            )
            .order_by("expiry_date")[:5]
        )
        top_items = (
            QuoteItem.objects.filter(quote__in=queryset, deleted_at__isnull=True)
            .values("description")
            .annotate(times_quoted=Count("id"), total_amount=Sum("line_total"))
            .order_by("-times_quoted")[:5]
        )
        monthly_raw = (
            queryset
            .annotate(month=TruncMonth("issue_date"))
            .values("month")
            .annotate(
                sent=Count("id", filter=Q(status__in=sent_statuses + [Quote.Status.EXPIRED])),
                accepted=Count("id", filter=Q(status=Quote.Status.ACCEPTED)),
            )
            .order_by("month")
        )
        conversion_series = [
            {
                "month": entry["month"].strftime("%b"),
                "sent": entry["sent"],
                "accepted": entry["accepted"],
                "rate": round(entry["accepted"] / entry["sent"] * 100, 1) if entry["sent"] else 0,
            }
            for entry in monthly_raw
        ]
        return Response(
            {
                "currency": currency,
                "total_quoted": total_quoted,
                "total_accepted": total_accepted,
                "conversion_rate": conversion_rate,
                "quotes_created": queryset.count(),
                "quotes_sent": queryset.filter(status__in=[Quote.Status.SENT, Quote.Status.VIEWED]).count(),
                "quotes_accepted": quotes_accepted,
                "quotes_rejected": queryset.filter(status=Quote.Status.REJECTED).count(),
                "avg_quote_value": queryset.aggregate(value=Avg("total"))["value"] or 0,
                "recent_quotes": QuoteListSerializer(recent_quotes, many=True).data,
                "expiring_quotes": QuoteListSerializer(expiring_quotes, many=True).data,
                "top_items": [
                    {
                        "item_name": item["description"],
                        "times_quoted": item["times_quoted"],
                        "total_amount": item["total_amount"] or 0,
                    }
                    for item in top_items
                ],
                "conversion_series": conversion_series,
            }
        )
