from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.users.views import ForgotPasswordView, LoginView, LogoutView, MeView, RefreshView, RegisterView, TenantUserViewSet


router = DefaultRouter()
router.register("users", TenantUserViewSet, basename="tenant-users")

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("refresh/", RefreshView.as_view(), name="refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("forgot-password/", ForgotPasswordView.as_view(), name="forgot-password"),
    path("me/", MeView.as_view(), name="me"),
    path("", include(router.urls)),
]
