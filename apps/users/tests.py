from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.tenants.models import Tenant
from apps.users.models import EmailVerificationToken, PasswordResetToken, User, UserManager
from apps.users.tokens import generate_token, hash_token

REGISTER_URL = "/api/auth/register/"
LOGIN_URL = "/api/auth/login/"
REFRESH_URL = "/api/auth/refresh/"
LOGOUT_URL = "/api/auth/logout/"
VERIFY_EMAIL_URL = "/api/auth/verify-email/"
RESEND_VERIFICATION_URL = "/api/auth/resend-verification/"
FORGOT_PASSWORD_URL = "/api/auth/forgot-password/"
RESET_PASSWORD_URL = "/api/auth/reset-password-confirm/"

COOKIE_OVERRIDES = dict(
    REFRESH_TOKEN_COOKIE_NAME="cotix_refresh_test",
    REFRESH_TOKEN_COOKIE_PATH="/api/auth/",
    REFRESH_TOKEN_COOKIE_SECURE=False,
    REFRESH_TOKEN_COOKIE_SAMESITE="Lax",
)


def make_tenant(**kwargs):
    defaults = dict(slug="test-tenant", business_name="Test Corp", email="corp@example.com")
    defaults.update(kwargs)
    return Tenant.objects.create(**defaults)


def make_user(tenant=None, email="user@example.com", password="Secure!Pass99", **kwargs):
    defaults = dict(role=User.Role.TENANT_ADMIN, is_active=True, email_verified=True)
    defaults.update(kwargs)
    return User.objects.create_user(email=email, password=password, tenant=tenant, **defaults)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class RegistrationTests(APITestCase):
    VALID_PAYLOAD = {
        "business_name": "Empresa Demo",
        "email": "nuevo@empresa.gt",
        "password": "Secure!Pass99",
        "first_name": "Juan",
        "last_name": "Perez",
    }

    def setUp(self):
        # Reset throttle counters: SimpleRateThrottle uses the Django cache backend.
        cache.clear()

    @patch("apps.users.views.send_verification_email")
    def test_successful_registration_returns_201(self, mock_send):
        response = self.client.post(REGISTER_URL, self.VALID_PAYLOAD, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("email", response.data)
        self.assertFalse(response.data["email_verified"])
        mock_send.assert_called_once()

    @patch("apps.users.views.send_verification_email")
    def test_creates_tenant_and_user(self, _mock):
        self.client.post(REGISTER_URL, self.VALID_PAYLOAD, format="json")

        user = User.objects.get(email="nuevo@empresa.gt")
        self.assertIsNotNone(user.tenant)
        self.assertEqual(user.tenant.business_name, "Empresa Demo")
        self.assertEqual(user.role, User.Role.TENANT_ADMIN)
        self.assertFalse(user.email_verified)

    @patch("apps.users.views.send_verification_email")
    def test_creates_verification_token(self, _mock):
        self.client.post(REGISTER_URL, self.VALID_PAYLOAD, format="json")

        user = User.objects.get(email="nuevo@empresa.gt")
        self.assertTrue(EmailVerificationToken.objects.filter(user=user).exists())

    @patch("apps.users.views.send_verification_email")
    def test_email_send_failure_does_not_block_registration(self, mock_send):
        mock_send.side_effect = Exception("SMTP down")
        response = self.client.post(REGISTER_URL, self.VALID_PAYLOAD, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_duplicate_email_returns_400(self):
        make_user(email="nuevo@empresa.gt", tenant=make_tenant())
        response = self.client.post(REGISTER_URL, self.VALID_PAYLOAD, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    def test_business_name_too_short_returns_400(self):
        payload = {**self.VALID_PAYLOAD, "business_name": "AB"}
        response = self.client.post(REGISTER_URL, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("business_name", response.data)

    def test_password_too_short_returns_400(self):
        payload = {**self.VALID_PAYLOAD, "password": "Short1!"}
        response = self.client.post(REGISTER_URL, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_common_password_rejected(self):
        payload = {**self.VALID_PAYLOAD, "password": "password123"}
        response = self.client.post(REGISTER_URL, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.data)

    def test_entirely_numeric_password_rejected(self):
        payload = {**self.VALID_PAYLOAD, "password": "12345678901"}
        response = self.client.post(REGISTER_URL, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.data)

    def test_password_equal_to_email_rejected(self):
        payload = {**self.VALID_PAYLOAD, "password": "nuevo@empresa.gt"}
        response = self.client.post(REGISTER_URL, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.data)

    def test_password_exceeding_max_length_rejected(self):
        payload = {**self.VALID_PAYLOAD, "password": "A1!" + "x" * 130}
        response = self.client.post(REGISTER_URL, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_nit_rejected(self):
        payload = {**self.VALID_PAYLOAD, "nit": "nit-invalido-##"}
        response = self.client.post(REGISTER_URL, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("nit", response.data)

    @patch("apps.users.views.send_verification_email")
    def test_valid_nit_cf_accepted(self, _mock):
        payload = {**self.VALID_PAYLOAD, "nit": "CF"}
        response = self.client.post(REGISTER_URL, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    @patch("apps.users.views.send_verification_email")
    def test_slug_collision_resolved_automatically(self, _mock):
        make_tenant(slug="empresa-demo", business_name="Empresa Demo", email="x@x.com")
        response = self.client.post(REGISTER_URL, self.VALID_PAYLOAD, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email="nuevo@empresa.gt")
        self.assertNotEqual(user.tenant.slug, "empresa-demo")

    @patch("apps.users.views.send_verification_email")
    def test_registration_is_atomic_rollback_on_failure(self, _mock):
        """If user creation fails after tenant creation, both are rolled back."""
        original_count = Tenant.objects.count()
        # patch.object on the class avoids brittle dotted-path Manager patching.
        with patch.object(UserManager, "create_user", side_effect=Exception("DB error")):
            response = self.client.post(REGISTER_URL, self.VALID_PAYLOAD, format="json")

        self.assertNotEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Tenant.objects.count(), original_count)


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------

class EmailVerificationTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = make_tenant()
        self.user = make_user(tenant=self.tenant, email="verify@example.com", email_verified=False)
        self.raw_token, token_hash = generate_token()
        EmailVerificationToken.objects.create(user=self.user, token_hash=token_hash)

    def test_valid_token_verifies_user(self):
        response = self.client.post(VERIFY_EMAIL_URL, {"token": self.raw_token}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_valid_token_marks_as_used(self):
        self.client.post(VERIFY_EMAIL_URL, {"token": self.raw_token}, format="json")

        token_obj = EmailVerificationToken.objects.get(user=self.user)
        self.assertIsNotNone(token_obj.used_at)

    def test_reusing_token_returns_400(self):
        self.client.post(VERIFY_EMAIL_URL, {"token": self.raw_token}, format="json")
        response = self.client.post(VERIFY_EMAIL_URL, {"token": self.raw_token}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_token_returns_400(self):
        response = self.client.post(VERIFY_EMAIL_URL, {"token": "garbage-token"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_expired_token_returns_400(self):
        token_obj = EmailVerificationToken.objects.get(user=self.user)
        token_obj.expires_at = timezone.now() - timedelta(hours=1)
        token_obj.save(update_fields=["expires_at"])

        response = self.client.post(VERIFY_EMAIL_URL, {"token": self.raw_token}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("apps.users.views.send_verification_email")
    def test_resend_creates_new_token(self, mock_send):
        response = self.client.post(RESEND_VERIFICATION_URL, {"email": self.user.email}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_send.assert_called_once()
        # Old raw token must now be invalid (replaced by a new one)
        cache.clear()
        verify_response = self.client.post(VERIFY_EMAIL_URL, {"token": self.raw_token}, format="json")
        self.assertEqual(verify_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_resend_unknown_email_still_returns_ok(self):
        """Anti-enumeration: always 200."""
        response = self.client.post(RESEND_VERIFICATION_URL, {"email": "unknown@x.com"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_resend_already_verified_user_returns_ok(self):
        self.user.email_verified = True
        self.user.save(update_fields=["email_verified"])

        response = self.client.post(RESEND_VERIFICATION_URL, {"email": self.user.email}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Login / email_verified gate
# ---------------------------------------------------------------------------

@override_settings(**COOKIE_OVERRIDES)
class LoginTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = make_tenant()

    def test_verified_user_can_login(self):
        make_user(tenant=self.tenant, email="ok@example.com", email_verified=True)
        response = self.client.post(LOGIN_URL, {"email": "ok@example.com", "password": "Secure!Pass99"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)

    def test_unverified_user_cannot_login(self):
        make_user(tenant=self.tenant, email="unverified@example.com", email_verified=False)
        response = self.client.post(LOGIN_URL, {"email": "unverified@example.com", "password": "Secure!Pass99"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("code", response.data)
        self.assertEqual(response.data["code"], "email_not_verified")

    def test_wrong_password_returns_error(self):
        make_user(tenant=self.tenant, email="ok@example.com", email_verified=True)
        response = self.client.post(LOGIN_URL, {"email": "ok@example.com", "password": "WrongPass!!"}, format="json")

        self.assertIn(response.status_code, [status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED])


# ---------------------------------------------------------------------------
# Forgot password
# ---------------------------------------------------------------------------

class ForgotPasswordTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = make_tenant()
        self.user = make_user(tenant=self.tenant, email="reset@example.com", email_verified=True)

    @patch("apps.users.views.send_password_reset_email")
    def test_known_email_triggers_email(self, mock_send):
        response = self.client.post(FORGOT_PASSWORD_URL, {"email": self.user.email}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_send.assert_called_once()

    @patch("apps.users.views.send_password_reset_email")
    def test_known_email_creates_reset_token(self, _mock):
        self.client.post(FORGOT_PASSWORD_URL, {"email": self.user.email}, format="json")

        self.assertTrue(PasswordResetToken.objects.filter(user=self.user).exists())

    def test_unknown_email_still_returns_ok(self):
        """Anti-enumeration: always 200."""
        response = self.client.post(FORGOT_PASSWORD_URL, {"email": "unknown@x.com"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch("apps.users.views.send_password_reset_email")
    def test_second_request_replaces_token(self, _mock):
        self.client.post(FORGOT_PASSWORD_URL, {"email": self.user.email}, format="json")
        first_hash = PasswordResetToken.objects.get(user=self.user).token_hash

        cache.clear()
        self.client.post(FORGOT_PASSWORD_URL, {"email": self.user.email}, format="json")
        second_hash = PasswordResetToken.objects.get(user=self.user).token_hash

        self.assertNotEqual(first_hash, second_hash)

    def test_unverified_user_does_not_get_reset_email(self):
        self.user.email_verified = False
        self.user.save(update_fields=["email_verified"])

        with patch("apps.users.views.send_password_reset_email") as mock_send:
            self.client.post(FORGOT_PASSWORD_URL, {"email": self.user.email}, format="json")

        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# Reset password confirm
# ---------------------------------------------------------------------------

class ResetPasswordConfirmTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = make_tenant()
        self.user = make_user(tenant=self.tenant, email="reset@example.com", email_verified=True)
        self.raw_token, token_hash = generate_token()
        PasswordResetToken.objects.create(user=self.user, token_hash=token_hash)

    def _post(self, payload):
        """Helper that clears cache before each call to avoid throttle hits."""
        cache.clear()
        return self.client.post(RESET_PASSWORD_URL, payload, format="json")

    def test_valid_token_updates_password(self):
        payload = {"token": self.raw_token, "password": "NewSecure!Pass9", "confirm_password": "NewSecure!Pass9"}
        response = self._post(payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewSecure!Pass9"))

    def test_valid_token_marks_used(self):
        payload = {"token": self.raw_token, "password": "NewSecure!Pass9", "confirm_password": "NewSecure!Pass9"}
        self._post(payload)

        token_obj = PasswordResetToken.objects.get(user=self.user)
        self.assertIsNotNone(token_obj.used_at)

    def test_reusing_token_returns_400(self):
        payload = {"token": self.raw_token, "password": "NewSecure!Pass9", "confirm_password": "NewSecure!Pass9"}
        self._post(payload)
        response = self._post(payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_expired_token_returns_400(self):
        token_obj = PasswordResetToken.objects.get(user=self.user)
        token_obj.expires_at = timezone.now() - timedelta(minutes=1)
        token_obj.save(update_fields=["expires_at"])

        payload = {"token": self.raw_token, "password": "NewSecure!Pass9", "confirm_password": "NewSecure!Pass9"}
        response = self._post(payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_token_returns_400(self):
        payload = {"token": "bad-token", "password": "NewSecure!Pass9", "confirm_password": "NewSecure!Pass9"}
        response = self._post(payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_mismatched_passwords_returns_400(self):
        payload = {"token": self.raw_token, "password": "NewSecure!Pass9", "confirm_password": "Different!Pass9"}
        response = self._post(payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("confirm_password", response.data)

    def test_weak_password_rejected(self):
        payload = {"token": self.raw_token, "password": "password123", "confirm_password": "password123"}
        response = self._post(payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.data)

    def test_numeric_only_password_rejected(self):
        payload = {"token": self.raw_token, "password": "12345678901", "confirm_password": "12345678901"}
        response = self._post(payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_password_equal_to_email_rejected(self):
        payload = {
            "token": self.raw_token,
            "password": "reset@example.com",
            "confirm_password": "reset@example.com",
        }
        response = self._post(payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# Auth cookie flow
# ---------------------------------------------------------------------------

@override_settings(**COOKIE_OVERRIDES)
class AuthCookieTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = make_tenant()
        self.user = make_user(tenant=self.tenant, email="admin@example.com", email_verified=True)

    def test_login_sets_httponly_refresh_cookie_without_exposing_refresh_json(self):
        response = self.client.post(LOGIN_URL, {"email": "admin@example.com", "password": "Secure!Pass99"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertNotIn("refresh", response.data)
        cookie = response.cookies[settings.REFRESH_TOKEN_COOKIE_NAME]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["path"], settings.REFRESH_TOKEN_COOKIE_PATH)
        self.assertEqual(cookie["samesite"], settings.REFRESH_TOKEN_COOKIE_SAMESITE)

    def test_refresh_reads_cookie_and_returns_only_access_token(self):
        login_response = self.client.post(LOGIN_URL, {"email": "admin@example.com", "password": "Secure!Pass99"}, format="json")
        self.client.cookies[settings.REFRESH_TOKEN_COOKIE_NAME] = login_response.cookies[settings.REFRESH_TOKEN_COOKIE_NAME].value

        response = self.client.post(REFRESH_URL, {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertNotIn("refresh", response.data)

    def test_refresh_rejects_requests_without_refresh_cookie(self):
        response = self.client.post(REFRESH_URL, {}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_logout_clears_refresh_cookie(self):
        response = self.client.post(LOGOUT_URL, {}, format="json")
        cookie = response.cookies[settings.REFRESH_TOKEN_COOKIE_NAME]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cookie.value, "")
        self.assertEqual(cookie["path"], settings.REFRESH_TOKEN_COOKIE_PATH)
        self.assertEqual(cookie["samesite"], settings.REFRESH_TOKEN_COOKIE_SAMESITE)


# ---------------------------------------------------------------------------
# Token utilities
# ---------------------------------------------------------------------------

class TokenUtilsTests(APITestCase):
    def test_generate_token_returns_raw_and_hash(self):
        raw, hashed = generate_token()
        self.assertIsInstance(raw, str)
        self.assertEqual(len(hashed), 64)  # SHA-256 hex digest
        self.assertEqual(hash_token(raw), hashed)

    def test_different_calls_produce_different_tokens(self):
        raw1, _ = generate_token()
        raw2, _ = generate_token()
        self.assertNotEqual(raw1, raw2)
