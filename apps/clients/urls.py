from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.clients.views import ClientViewSet


router = DefaultRouter()
router.register("", ClientViewSet, basename="clients")

urlpatterns = [path("", include(router.urls))]
