from django.core.cache import cache
from rest_framework.test import APITestCase

from apps.telemetry.models import UserSession
from apps.tenants.models import Tenant
from apps.users.models import PasswordResetToken, User
from apps.users.tokens import generate_token


class SessionSecurityTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(slug="security-tenant", business_name="Security", email="security@example.com")
        self.user = User.objects.create_user(
            email="security@example.com", password="Secure!Pass99", tenant=self.tenant,
            role=User.Role.TENANT_ADMIN, email_verified=True,
        )

    def login(self):
        return self.client.post("/api/auth/login/", {
            "email": self.user.email, "password": "Secure!Pass99"
        }, format="json")

    def test_logout_revokes_access_and_refresh(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        access = response.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 200)
        self.assertEqual(self.client.post("/api/auth/logout/").status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)
        self.client.credentials()
        self.client.cookies["cotix_refresh"] = response.cookies["cotix_refresh"].value
        self.assertEqual(self.client.post("/api/auth/refresh/").status_code, 401)

    def test_revoking_one_session_does_not_revoke_another(self):
        first = self.login()
        first_cookie = first.cookies["cotix_refresh"].value
        second = self.login()
        second_access = second.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {second_access}")
        session = UserSession.objects.filter(user=self.user).order_by("login_at").first()
        self.assertEqual(self.client.delete(f"/api/telemetry/sessions/{session.pk}/").status_code, 200)
        self.client.credentials()
        self.client.cookies["cotix_refresh"] = first_cookie
        self.assertEqual(self.client.post("/api/auth/refresh/").status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {second_access}")
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 200)

    def test_deleted_user_cannot_login_refresh_or_use_access(self):
        response = self.login()
        access = response.data["access"]
        cookie = response.cookies["cotix_refresh"].value
        self.user.soft_delete()
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertTrue(UserSession.objects.filter(user=self.user, revoked_at__isnull=False).exists())
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)
        self.client.credentials()
        self.client.cookies["cotix_refresh"] = cookie
        self.assertEqual(self.client.post("/api/auth/refresh/").status_code, 401)
        self.assertNotEqual(self.login().status_code, 200)

    def test_password_reset_revokes_existing_session(self):
        response = self.login()
        access = response.data["access"]
        cookie = response.cookies["cotix_refresh"].value
        raw_token, token_hash = generate_token()
        PasswordResetToken.objects.create(user=self.user, token_hash=token_hash)
        reset = self.client.post("/api/auth/reset-password-confirm/", {
            "token": raw_token, "password": "NewSecure!Pass99", "confirm_password": "NewSecure!Pass99",
        }, format="json")
        self.assertEqual(reset.status_code, 200)
        self.assertFalse(UserSession.objects.filter(user=self.user, revoked_at__isnull=True).exists())
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)
        self.client.credentials()
        self.client.cookies["cotix_refresh"] = cookie
        self.assertEqual(self.client.post("/api/auth/refresh/").status_code, 401)
