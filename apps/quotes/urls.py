from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.quotes.views import DashboardReportView, PublicQuoteRespondView, PublicQuoteView, QuoteViewSet


router = DefaultRouter()
router.register("quotes", QuoteViewSet, basename="quotes")

urlpatterns = [
    path("", include(router.urls)),
    path("reports/dashboard/", DashboardReportView.as_view(), name="reports-dashboard"),
    path("public/quotes/<str:token>/", PublicQuoteView.as_view(), name="public-quote"),
    path("public/quotes/<str:token>/respond/", PublicQuoteRespondView.as_view(), name="public-quote-respond"),
]
