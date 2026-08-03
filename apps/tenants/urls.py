from django.urls import path

from apps.tenants.views import (
    BillingCheckoutView,
    BillingPortalView,
    LogoUploadView,
    TenantSettingsView,
)


urlpatterns = [
    path("settings/", TenantSettingsView.as_view(), name="tenant-settings"),
    path("settings/logo/", LogoUploadView.as_view(), name="tenant-logo-upload"),
    path("billing/checkout/", BillingCheckoutView.as_view(), name="billing-checkout"),
    path("billing/portal/", BillingPortalView.as_view(), name="billing-portal"),
]
