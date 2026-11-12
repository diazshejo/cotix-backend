from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.common.views import healthcheck
from apps.tenants.views import BillingWebhookView, public_tenant_logo


urlpatterns = [
    path("media/logos/<uuid:tenant_id>.<str:extension>", public_tenant_logo, name="public-tenant-logo"),
    path("admin/", admin.site.urls),
    path("api/health/", healthcheck, name="healthcheck"),
    path("api/billing/webhook/", BillingWebhookView.as_view(), name="billing-webhook"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/auth/", include("apps.users.urls")),
    path("api/telemetry/", include("apps.telemetry.urls")),
    path("api/tenant/", include("apps.tenants.urls")),
    path("api/clients/", include("apps.clients.urls")),
    path("api/catalog/", include("apps.catalog.urls")),
    path("api/integrations/", include("apps.integrations.urls")),
    path("api/", include("apps.quotes.urls")),
    path("api/", include("apps.notifications.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
