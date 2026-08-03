from django.urls import path

from apps.telemetry.views import ActiveSessionsView, AuthEventListView, FailedLoginSummaryView

urlpatterns = [
    path("sessions/", ActiveSessionsView.as_view(), name="telemetry-sessions"),
    path("sessions/<uuid:session_id>/", ActiveSessionsView.as_view(), name="telemetry-session-detail"),
    path("auth-events/", AuthEventListView.as_view(), name="telemetry-auth-events"),
    path("failed-logins/", FailedLoginSummaryView.as_view(), name="telemetry-failed-logins"),
]
