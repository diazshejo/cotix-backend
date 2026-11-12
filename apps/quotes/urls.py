from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.quotes.views import DashboardReportView, PlanUsageView, PublicQuotePdfView, PublicQuoteRespondView, PublicQuoteView, QuoteItemViewSet, QuoteViewSet


router = DefaultRouter()
router.register("quotes", QuoteViewSet, basename="quotes")

urlpatterns = [
    # plan-usage antes del router para evitar colisión con /quotes/{pk}/
    path("quotes/plan-usage/", PlanUsageView.as_view(), name="quotes-plan-usage"),
    path("", include(router.urls)),
    path("quotes/<str:quote_pk>/items/",
         QuoteItemViewSet.as_view({"get": "list", "post": "create"}),
         name="quote-items-list"),
    path("quotes/<str:quote_pk>/items/<str:pk>/",
         QuoteItemViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"}),
         name="quote-items-detail"),
    path("reports/dashboard/", DashboardReportView.as_view(), name="reports-dashboard"),
    path("public/quotes/<str:token>/", PublicQuoteView.as_view(), name="public-quote"),
    path("public/quotes/<str:token>/respond/", PublicQuoteRespondView.as_view(), name="public-quote-respond"),
    path("public/quotes/<str:token>/pdf/", PublicQuotePdfView.as_view(), name="public-quote-pdf"),
]
