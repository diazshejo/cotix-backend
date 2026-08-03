from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.integrations.views import IntegrationSyncLogViewSet, TenantCatalogIntegrationViewSet


router = DefaultRouter()
router.register("catalog", TenantCatalogIntegrationViewSet, basename="catalog-integrations")
router.register("catalog-sync-logs", IntegrationSyncLogViewSet, basename="catalog-integration-sync-logs")

urlpatterns = [path("", include(router.urls))]
