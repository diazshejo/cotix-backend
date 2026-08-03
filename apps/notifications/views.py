from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.notifications.models import Notification
from apps.notifications.serializers import NotificationSerializer


class NotificationPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class NotificationViewSet(viewsets.GenericViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = NotificationPagination

    def get_queryset(self):
        return (
            Notification.objects.filter(user=self.request.user)
            .select_related("quote")
            .order_by("-created_at")
        )

    def list(self, request):
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response(NotificationSerializer(page, many=True).data)
        return Response(NotificationSerializer(queryset, many=True).data)

    @action(detail=False, methods=["get"], url_path="unread_count")
    def unread_count(self, request):
        count = self.get_queryset().filter(is_read=False).count()
        return Response({"unread_count": count})

    @action(detail=False, methods=["get"], url_path="recent")
    def recent(self, request):
        qs = self.get_queryset()[:10]
        return Response(NotificationSerializer(qs, many=True).data)

    @action(detail=True, methods=["post"], url_path="mark_as_read")
    def mark_as_read(self, request, pk=None):
        notification = self.get_queryset().filter(pk=pk).first()
        if not notification:
            return Response({"detail": "No encontrada."}, status=status.HTTP_404_NOT_FOUND)
        notification.mark_as_read()
        return Response(NotificationSerializer(notification).data)

    @action(detail=False, methods=["post"], url_path="mark_all_as_read")
    def mark_all_as_read(self, request):
        from django.utils import timezone
        now = timezone.now()
        updated = self.get_queryset().filter(is_read=False).update(
            is_read=True,
            read_at=now,
            updated_at=now,
        )
        return Response({"updated": updated})

    @action(detail=False, methods=["delete"], url_path="clear_all")
    def clear_all(self, request):
        deleted, _ = self.get_queryset().delete()
        return Response({"deleted": deleted}, status=status.HTTP_200_OK)
