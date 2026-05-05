from django.db import transaction
from django.db.models import Avg, Count, Sum
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
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
    QuoteSerializer,
)
from apps.quotes.services import (
    is_quote_expired,
    log_quote_activity,
    next_quote_number,
    recalculate_quote,
    validate_quote_ready,
    validate_status_transition,
)


class QuoteViewSet(TenantScopedQuerysetMixin, viewsets.ModelViewSet):
    queryset = Quote.objects.select_related("tenant", "client", "created_by", "assigned_to").prefetch_related("items")
    serializer_class = QuoteSerializer
    permission_classes = [IsAuthenticated, TenantRolePermission]

    def perform_create(self, serializer):
        serializer.save()

    def perform_destroy(self, instance):
        if instance.status != Quote.Status.DRAFT:
            raise ValidationError("Only draft quotes can be deleted.")
        instance.soft_delete()
        log_quote_activity(instance, "deleted", actor=self.request.user, message="Quote deleted.")

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def duplicate(self, request, pk=None):
        source = self.get_object()
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
            tax_rate_snapshot=source.tenant.tax_rate,
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


class PublicQuoteView(APIView):
    permission_classes = [AllowAny]

    def get_quote(self, token):
        return get_object_or_404(Quote.objects.select_related("tenant", "client").prefetch_related("items"),
            public_token=token,
            deleted_at__isnull=True,
        )

    throttle_classes = [PublicQuoteViewThrottle]

    @extend_schema(responses=QuoteSerializer)
    def get(self, request, token):
        quote = self.get_quote(token)
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
    def post(self, request, token):
        quote = get_object_or_404(Quote, public_token=token, deleted_at__isnull=True)
        serializer = PublicQuoteResponseSerializer(data=request.data, context={"quote": quote, "request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"status": quote.status})


class DashboardReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get_queryset(self, request):
        queryset = Quote.objects.filter(deleted_at__isnull=True)
        if request.user.is_super_admin:
            return queryset
        return queryset.filter(tenant=request.user.tenant)

    def get(self, request):
        queryset = self.get_queryset(request)
        total_quoted = queryset.aggregate(value=Sum("total"))["value"] or 0
        total_accepted = queryset.filter(status=Quote.Status.ACCEPTED).aggregate(value=Sum("total"))["value"] or 0
        quotes_sent = queryset.filter(status__in=[Quote.Status.SENT, Quote.Status.VIEWED, Quote.Status.ACCEPTED, Quote.Status.REJECTED]).count()
        quotes_accepted = queryset.filter(status=Quote.Status.ACCEPTED).count()
        conversion_rate = round((quotes_accepted / quotes_sent) * 100, 2) if quotes_sent else 0
        recent_quotes = queryset.select_related("client").prefetch_related("items").order_by("-created_at")[:5]
        expiring_quotes = queryset.select_related("client").prefetch_related("items").filter(
            status__in=[Quote.Status.DRAFT, Quote.Status.SENT, Quote.Status.VIEWED],
            expiry_date__gte=timezone.localdate(),
        ).order_by("expiry_date")[:5]
        top_items = (
            QuoteItem.objects.filter(quote__in=queryset, deleted_at__isnull=True)
            .values("description")
            .annotate(times_quoted=Count("id"), total_amount=Sum("line_total"))
            .order_by("-times_quoted")[:5]
        )

        return Response(
            {
                "total_quoted": total_quoted,
                "total_accepted": total_accepted,
                "conversion_rate": conversion_rate,
                "quotes_created": queryset.count(),
                "quotes_sent": queryset.filter(status__in=[Quote.Status.SENT, Quote.Status.VIEWED]).count(),
                "quotes_accepted": quotes_accepted,
                "quotes_rejected": queryset.filter(status=Quote.Status.REJECTED).count(),
                "avg_quote_value": queryset.aggregate(value=Avg("total"))["value"] or 0,
                "recent_quotes": QuoteSerializer(recent_quotes, many=True, context={"request": request}).data,
                "expiring_quotes": QuoteSerializer(expiring_quotes, many=True, context={"request": request}).data,
                "top_items": [
                    {
                        "item_name": item["description"],
                        "times_quoted": item["times_quoted"],
                        "total_amount": item["total_amount"] or 0,
                    }
                    for item in top_items
                ],
                "conversion_series": [],
            }
        )
