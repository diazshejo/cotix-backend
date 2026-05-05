from django.conf import settings
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.tenants.models import Tenant
from apps.users.models import User


@override_settings(
    REFRESH_TOKEN_COOKIE_NAME="cotix_refresh_test",
    REFRESH_TOKEN_COOKIE_PATH="/api/auth/",
    REFRESH_TOKEN_COOKIE_SECURE=False,
    REFRESH_TOKEN_COOKIE_SAMESITE="Lax",
)
class AuthCookieTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            slug="tenant-auth-tests",
            business_name="Tenant Auth Tests",
            email="tenant@example.com",
        )
        self.user = User.objects.create_user(
            email="admin@example.com",
            password="password123",
            role=User.Role.TENANT_ADMIN,
            tenant=self.tenant,
            is_active=True,
        )

    def test_login_sets_httponly_refresh_cookie_without_exposing_refresh_json(self):
        response = self.client.post(
            "/api/auth/login/",
            {"email": "admin@example.com", "password": "password123"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertNotIn("refresh", response.data)

        cookie = response.cookies[settings.REFRESH_TOKEN_COOKIE_NAME]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["path"], settings.REFRESH_TOKEN_COOKIE_PATH)
        self.assertEqual(cookie["samesite"], settings.REFRESH_TOKEN_COOKIE_SAMESITE)

    def test_refresh_reads_cookie_and_returns_only_access_token(self):
        login_response = self.client.post(
            "/api/auth/login/",
            {"email": "admin@example.com", "password": "password123"},
            format="json",
        )
        self.client.cookies[settings.REFRESH_TOKEN_COOKIE_NAME] = login_response.cookies[
            settings.REFRESH_TOKEN_COOKIE_NAME
        ].value

        response = self.client.post("/api/auth/refresh/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertNotIn("refresh", response.data)

    def test_refresh_rejects_requests_without_refresh_cookie(self):
        response = self.client.post("/api/auth/refresh/", {}, format="json")

        self.assertEqual(response.status_code, 401)

    def test_logout_clears_refresh_cookie(self):
        response = self.client.post("/api/auth/logout/", {}, format="json")

        cookie = response.cookies[settings.REFRESH_TOKEN_COOKIE_NAME]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cookie.value, "")
        self.assertEqual(cookie["path"], settings.REFRESH_TOKEN_COOKIE_PATH)
        self.assertEqual(cookie["samesite"], settings.REFRESH_TOKEN_COOKIE_SAMESITE)
