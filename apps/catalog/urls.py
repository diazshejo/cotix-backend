from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.catalog.views import CatalogCategoryViewSet, CatalogItemViewSet


router = DefaultRouter()
router.register("categories", CatalogCategoryViewSet, basename="catalog-categories")
router.register("items", CatalogItemViewSet, basename="catalog-items")

urlpatterns = [path("", include(router.urls))]
