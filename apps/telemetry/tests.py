from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.telemetry.models import AuthEvent, UserSession
from apps.telemetry.services.auth_telemetry import log_auth_event
from apps.telemetry.services.risk import compute_risk_score
from apps.telemetry.services.session import (
    compute_device_fingerprint,
    create_session,
    is_known_device,
    touch_session,
)
from apps.telemetry.services.user_agent import parse_user_agent
from apps.tenants.models import Tenant
from apps.users.models import User

# ── URL constants ─────────────────────────────────────────────────────────────
SESSIONS_URL = "/api/telemetry/sessions/"
AUTH_EVENTS_URL = "/api/telemetry/auth-events/"
FAILED_LOGINS_URL = "/api/telemetry/failed-logins/"
LOGIN_URL = "/api/auth/login/"
LOGOUT_URL = "/api/auth/logout/"

# ── UA string fixtures ────────────────────────────────────────────────────────
UA_CHROME_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
UA_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.4 Mobile/15E148 Safari/604.1"
)
UA_IPAD = (
    "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.4 Mobile/15E148 Safari/604.1"
)
UA_GOOGLEBOT = (
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_tenant(**kwargs):
    defaults = dict(slug="test-tenant", business_name="Test Corp", email="corp@example.com")
    defaults.update(kwargs)
    return Tenant.objects.create(**defaults)


def make_user(tenant=None, email="user@example.com", password="Secure!Pass99", **kwargs):
    defaults = dict(role=User.Role.TENANT_ADMIN, is_active=True, email_verified=True)
    defaults.update(kwargs)
    return User.objects.create_user(email=email, password=password, tenant=tenant, **defaults)


# ─────────────────────────────────────────────────────────────────────────────
# 1. UAData parsing tests
# ─────────────────────────────────────────────────────────────────────────────

class ParseUserAgentTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_desktop_chrome(self):
        result = parse_user_agent(UA_CHROME_DESKTOP)
        self.assertEqual(result["device_type"], "desktop")
        self.assertIn("Chrome", result["browser"])
        self.assertFalse(result["is_mobile"])
        self.assertNotEqual(result["browser_version"], "")

    def test_mobile_iphone(self):
        result = parse_user_agent(UA_IPHONE)
        self.assertEqual(result["device_type"], "mobile")
        self.assertTrue(result["is_mobile"])
        self.assertNotEqual(result["browser"], "")
        self.assertNotEqual(result["os"], "")

    def test_tablet_ipad(self):
        result = parse_user_agent(UA_IPAD)
        # iPads may be classified as tablet or mobile depending on UA string
        self.assertIn(result["device_type"], ("tablet", "mobile"))

    def test_bot_googlebot(self):
        result = parse_user_agent(UA_GOOGLEBOT)
        self.assertEqual(result["device_type"], "bot")

    def test_empty_string_returns_empty(self):
        result = parse_user_agent("")
        self.assertEqual(result["browser"], "")
        self.assertEqual(result["os"], "")
        self.assertEqual(result["device_type"], "unknown")
        self.assertEqual(result["browser_version"], "")
        self.assertFalse(result["is_mobile"])

    def test_whitespace_only_returns_empty(self):
        result = parse_user_agent("   ")
        self.assertEqual(result["device_type"], "unknown")

    def test_malformed_string_does_not_raise(self):
        # Should return a dict without raising
        result = parse_user_agent("not a real UA string !!@#$%^")
        self.assertIn("device_type", result)
        self.assertIsInstance(result["is_mobile"], bool)

    def test_browser_field_max_length(self):
        long_ua = "A" * 1000
        result = parse_user_agent(long_ua)
        self.assertLessEqual(len(result["browser"]), 60)
        self.assertLessEqual(len(result["os"]), 60)

    def test_returns_copy_not_singleton(self):
        r1 = parse_user_agent("")
        r2 = parse_user_agent("")
        r1["browser"] = "mutated"
        self.assertNotEqual(r2["browser"], "mutated")


# ─────────────────────────────────────────────────────────────────────────────
# 2. compute_device_fingerprint tests
# ─────────────────────────────────────────────────────────────────────────────

class ComputeDeviceFingerprintTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_deterministic(self):
        fp1 = compute_device_fingerprint("Chrome", "Windows", UA_CHROME_DESKTOP)
        fp2 = compute_device_fingerprint("Chrome", "Windows", UA_CHROME_DESKTOP)
        self.assertEqual(fp1, fp2)

    def test_different_browser_different_fp(self):
        fp1 = compute_device_fingerprint("Chrome", "Windows", UA_CHROME_DESKTOP)
        fp2 = compute_device_fingerprint("Firefox", "Windows", UA_CHROME_DESKTOP)
        self.assertNotEqual(fp1, fp2)

    def test_different_os_different_fp(self):
        fp1 = compute_device_fingerprint("Chrome", "Windows", UA_CHROME_DESKTOP)
        fp2 = compute_device_fingerprint("Chrome", "macOS", UA_CHROME_DESKTOP)
        self.assertNotEqual(fp1, fp2)

    def test_different_ua_different_fp(self):
        fp1 = compute_device_fingerprint("Safari", "iOS", UA_IPHONE)
        fp2 = compute_device_fingerprint("Safari", "iOS", UA_IPAD)
        self.assertNotEqual(fp1, fp2)

    def test_output_is_32_chars(self):
        fp = compute_device_fingerprint("Chrome", "Windows", UA_CHROME_DESKTOP)
        self.assertEqual(len(fp), 32)

    def test_empty_inputs_do_not_raise(self):
        fp = compute_device_fingerprint("", "", "")
        self.assertIsInstance(fp, str)
        self.assertEqual(len(fp), 32)

    def test_ua_truncated_at_200(self):
        # Two UAs that differ only after the 200-char mark should produce same fp
        base = "A" * 200
        fp1 = compute_device_fingerprint("Chrome", "Win", base + "EXTRA1")
        fp2 = compute_device_fingerprint("Chrome", "Win", base + "EXTRA2")
        self.assertEqual(fp1, fp2)


# ─────────────────────────────────────────────────────────────────────────────
# 3. compute_risk_score tests
# ─────────────────────────────────────────────────────────────────────────────

class ComputeRiskScoreTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_login_success_no_factors_zero(self):
        score = compute_risk_score("login_success")
        self.assertEqual(score, 0)

    def test_login_failed_base_score(self):
        # base=20, success=False adds +10
        score = compute_risk_score("login_failed", success=False)
        self.assertEqual(score, 30)

    def test_login_blocked(self):
        score = compute_risk_score("login_blocked", success=False)
        # base=40 + 10 (not success) = 50
        self.assertEqual(score, 50)

    def test_account_locked(self):
        score = compute_risk_score("account_locked")
        self.assertEqual(score, 60)

    def test_suspicious_login(self):
        score = compute_risk_score("suspicious_login")
        self.assertEqual(score, 50)

    def test_new_device_adds_25(self):
        score = compute_risk_score("login_success", is_new_device=True)
        self.assertEqual(score, 25)

    def test_multiple_factors_accumulate(self):
        score = compute_risk_score(
            "login_failed",
            success=False,
            is_new_device=True,
            risk_factors=["new_ip", "unusual_hour"],
        )
        # base=20 + 10(fail) + 25(new_device) + 10(new_ip) + 10(unusual_hour) = 75
        self.assertEqual(score, 75)

    def test_capped_at_100(self):
        score = compute_risk_score(
            "account_locked",
            success=False,
            is_new_device=True,
            risk_factors=["new_ip", "rapid_successive", "unusual_hour"],
        )
        self.assertLessEqual(score, 100)

    def test_unknown_event_type_zero_base(self):
        score = compute_risk_score("some_unknown_event")
        self.assertEqual(score, 0)

    def test_unknown_factor_ignored(self):
        score = compute_risk_score("login_success", risk_factors=["totally_unknown_factor"])
        self.assertEqual(score, 0)

    def test_empty_risk_factors_list(self):
        score = compute_risk_score("login_success", risk_factors=[])
        self.assertEqual(score, 0)

    def test_none_risk_factors(self):
        score = compute_risk_score("login_success", risk_factors=None)
        self.assertEqual(score, 0)


# ─────────────────────────────────────────────────────────────────────────────
# 4. is_known_device tests
# ─────────────────────────────────────────────────────────────────────────────

class IsKnownDeviceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = make_tenant()
        self.user = make_user(tenant=self.tenant)

    def test_known_device_active_session(self):
        fp = compute_device_fingerprint("Chrome", "Windows", UA_CHROME_DESKTOP)
        UserSession.objects.create(
            user=self.user,
            tenant=self.tenant,
            device_fingerprint=fp,
        )
        self.assertTrue(is_known_device(self.user, fp))

    def test_unknown_device_no_session(self):
        fp = compute_device_fingerprint("Safari", "macOS", UA_IPHONE)
        self.assertFalse(is_known_device(self.user, fp))

    def test_revoked_session_not_known(self):
        fp = compute_device_fingerprint("Chrome", "Windows", UA_CHROME_DESKTOP)
        UserSession.objects.create(
            user=self.user,
            tenant=self.tenant,
            device_fingerprint=fp,
            revoked_at=timezone.now(),
        )
        self.assertFalse(is_known_device(self.user, fp))

    def test_empty_fingerprint_returns_false(self):
        self.assertFalse(is_known_device(self.user, ""))

    def test_none_user_returns_false(self):
        fp = compute_device_fingerprint("Chrome", "Windows", UA_CHROME_DESKTOP)
        self.assertFalse(is_known_device(None, fp))

    def test_other_user_session_does_not_match(self):
        other_user = make_user(
            tenant=self.tenant,
            email="other@example.com",
        )
        fp = compute_device_fingerprint("Chrome", "Windows", UA_CHROME_DESKTOP)
        UserSession.objects.create(
            user=other_user,
            tenant=self.tenant,
            device_fingerprint=fp,
        )
        self.assertFalse(is_known_device(self.user, fp))


# ─────────────────────────────────────────────────────────────────────────────
# 5. create_session tests
# ─────────────────────────────────────────────────────────────────────────────

class CreateSessionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = make_tenant()
        self.user = make_user(tenant=self.tenant)

    def test_creates_session_in_db(self):
        session = create_session(
            self.user,
            ip_address="192.168.1.1",
            browser="Chrome",
            os="Windows",
            device_type="desktop",
            user_agent=UA_CHROME_DESKTOP,
        )
        self.assertIsNotNone(session)
        self.assertEqual(UserSession.objects.filter(user=self.user).count(), 1)

    def test_session_fields_populated(self):
        session = create_session(
            self.user,
            ip_address="10.0.0.1",
            browser="Firefox",
            os="Linux",
            device_type="desktop",
            user_agent="Mozilla/5.0 Firefox",
        )
        self.assertEqual(session.user, self.user)
        self.assertEqual(session.tenant, self.tenant)
        self.assertEqual(session.browser, "Firefox")
        self.assertEqual(session.os, "Linux")
        self.assertEqual(session.ip_address, "10.0.0.1")

    def test_fingerprint_is_set(self):
        session = create_session(
            self.user,
            browser="Chrome",
            os="Windows",
            user_agent=UA_CHROME_DESKTOP,
        )
        expected_fp = compute_device_fingerprint("Chrome", "Windows", UA_CHROME_DESKTOP)
        self.assertEqual(session.device_fingerprint, expected_fp)

    def test_metadata_stored(self):
        session = create_session(
            self.user,
            metadata={"source": "mobile_app"},
        )
        self.assertEqual(session.metadata, {"source": "mobile_app"})

    def test_empty_metadata_defaults_to_dict(self):
        session = create_session(self.user)
        self.assertEqual(session.metadata, {})

    def test_handles_db_error_gracefully(self):
        from django.db import DatabaseError
        with patch("apps.telemetry.models.UserSession.objects.create", side_effect=DatabaseError("boom")):
            session = create_session(self.user)
        self.assertIsNone(session)

    def test_no_tenant_user(self):
        user_no_tenant = make_user(tenant=None, email="notenant@example.com")
        session = create_session(user_no_tenant)
        self.assertIsNotNone(session)
        self.assertIsNone(session.tenant)


# ─────────────────────────────────────────────────────────────────────────────
# 6. touch_session tests
# ─────────────────────────────────────────────────────────────────────────────

class TouchSessionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = make_tenant()
        self.user = make_user(tenant=self.tenant)
        self.session = create_session(self.user)

    def test_updates_when_past_threshold(self):
        # Force last_seen_at to be old (beyond threshold)
        old_time = timezone.now() - timedelta(seconds=600)
        UserSession.objects.filter(pk=self.session.pk).update(last_seen_at=old_time)

        touch_session(self.session, min_interval_seconds=300)

        self.session.refresh_from_db()
        self.assertGreater(self.session.last_seen_at, old_time)

    def test_skips_when_recent(self):
        # Set last_seen_at to very recent time (within threshold)
        recent_time = timezone.now() - timedelta(seconds=10)
        UserSession.objects.filter(pk=self.session.pk).update(last_seen_at=recent_time)

        touch_session(self.session, min_interval_seconds=300)

        self.session.refresh_from_db()
        # Should remain close to recent_time (within a few seconds)
        diff = abs((self.session.last_seen_at - recent_time).total_seconds())
        self.assertLess(diff, 5)

    def test_custom_interval(self):
        # With interval=0, any session should be updated
        old_time = timezone.now() - timedelta(seconds=1)
        UserSession.objects.filter(pk=self.session.pk).update(last_seen_at=old_time)

        touch_session(self.session, min_interval_seconds=0)

        self.session.refresh_from_db()
        self.assertGreater(self.session.last_seen_at, old_time)


# ─────────────────────────────────────────────────────────────────────────────
# 7. log_auth_event tests
# ─────────────────────────────────────────────────────────────────────────────

class LogAuthEventTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = make_tenant()
        self.user = make_user(tenant=self.tenant)

    def test_creates_auth_event(self):
        event = log_auth_event("login_success", user=self.user)
        self.assertIsNotNone(event)
        self.assertEqual(AuthEvent.objects.count(), 1)

    def test_sets_correct_event_type(self):
        event = log_auth_event("login_failed", user=self.user, success=False)
        self.assertEqual(event.event_type, "login_failed")
        self.assertFalse(event.success)

    def test_inherits_tenant_from_user(self):
        event = log_auth_event("login_success", user=self.user)
        self.assertEqual(event.tenant, self.tenant)

    def test_explicit_tenant_overrides(self):
        other_tenant = make_tenant(slug="other-tenant", business_name="Other", email="other@x.com")
        event = log_auth_event("login_success", user=self.user, tenant=other_tenant)
        self.assertEqual(event.tenant, other_tenant)

    def test_extracts_ip_from_request(self):
        from django.test import RequestFactory
        rf = RequestFactory()
        request = rf.get("/", HTTP_USER_AGENT=UA_CHROME_DESKTOP, REMOTE_ADDR="1.2.3.4")
        event = log_auth_event("login_success", request=request, user=self.user)
        self.assertEqual(event.ip_address, "1.2.3.4")

    def test_extracts_ip_from_forwarded_header(self):
        from django.test import RequestFactory
        rf = RequestFactory()
        request = rf.get(
            "/",
            HTTP_X_FORWARDED_FOR="203.0.113.1, 10.0.0.1",
            HTTP_USER_AGENT=UA_CHROME_DESKTOP,
        )
        event = log_auth_event("login_success", request=request, user=self.user)
        self.assertEqual(event.ip_address, "203.0.113.1")

    def test_parses_ua_fields(self):
        from django.test import RequestFactory
        rf = RequestFactory()
        request = rf.get("/", HTTP_USER_AGENT=UA_CHROME_DESKTOP)
        event = log_auth_event("login_success", request=request, user=self.user)
        self.assertIn("Chrome", event.browser)
        self.assertEqual(event.device_type, "desktop")

    def test_risk_score_computed(self):
        event = log_auth_event("login_failed", user=self.user, success=False)
        self.assertGreater(event.risk_score, 0)

    def test_metadata_geo_placeholder(self):
        event = log_auth_event("login_success", user=self.user)
        self.assertIn("geo", event.metadata)
        self.assertIsNone(event.metadata["geo"]["country_code"])

    def test_metadata_merged(self):
        event = log_auth_event(
            "login_success",
            user=self.user,
            metadata={"custom_key": "custom_value"},
        )
        self.assertEqual(event.metadata["custom_key"], "custom_value")

    def test_session_linked(self):
        session = create_session(self.user)
        event = log_auth_event("login_success", user=self.user, session=session)
        self.assertEqual(event.session, session)

    def test_never_raises_on_db_error(self):
        from django.db import DatabaseError
        with patch("apps.telemetry.models.AuthEvent.objects.create", side_effect=DatabaseError("fail")):
            result = log_auth_event("login_success", user=self.user)
        self.assertIsNone(result)

    def test_never_raises_on_unexpected_error(self):
        with patch("apps.telemetry.services.auth_telemetry._extract_request_context", side_effect=RuntimeError("boom")):
            result = log_auth_event("login_success", user=self.user)
        self.assertIsNone(result)

    def test_no_user_no_request_still_creates(self):
        event = log_auth_event("login_failed", success=False)
        self.assertIsNotNone(event)
        self.assertIsNone(event.user)
        self.assertIsNone(event.tenant)

    def test_risk_factor_new_device_in_metadata(self):
        event = log_auth_event(
            "new_device_login",
            user=self.user,
            metadata={"is_new_device": True, "risk_factors": ["new_device"]},
        )
        self.assertGreater(event.risk_score, 0)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Login success integration test: session + auth event
# ─────────────────────────────────────────────────────────────────────────────

class LoginSuccessIntegrationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = make_tenant()
        self.user = make_user(tenant=self.tenant)

    def test_create_session_and_auth_event(self):
        session = create_session(
            self.user,
            ip_address="192.168.0.1",
            browser="Chrome",
            os="Windows",
            device_type="desktop",
            user_agent=UA_CHROME_DESKTOP,
        )
        event = log_auth_event(
            "login_success",
            user=self.user,
            session=session,
        )
        self.assertIsNotNone(session)
        self.assertIsNotNone(event)
        self.assertEqual(event.session, session)
        self.assertEqual(event.event_type, "login_success")
        self.assertTrue(event.success)
        self.assertEqual(UserSession.objects.filter(user=self.user).count(), 1)
        self.assertEqual(AuthEvent.objects.filter(user=self.user).count(), 1)


# ─────────────────────────────────────────────────────────────────────────────
# 9. Login with new device: NEW_DEVICE_LOGIN event
# ─────────────────────────────────────────────────────────────────────────────

class NewDeviceLoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = make_tenant()
        self.user = make_user(tenant=self.tenant)

    def test_new_device_event_type(self):
        fp = compute_device_fingerprint("Chrome", "Windows", UA_CHROME_DESKTOP)
        self.assertFalse(is_known_device(self.user, fp))

        session = create_session(
            self.user,
            browser="Chrome",
            os="Windows",
            user_agent=UA_CHROME_DESKTOP,
        )
        event = log_auth_event(
            AuthEvent.EventType.NEW_DEVICE_LOGIN,
            user=self.user,
            session=session,
            metadata={"is_new_device": True, "risk_factors": ["new_device"]},
        )
        self.assertEqual(event.event_type, AuthEvent.EventType.NEW_DEVICE_LOGIN)
        self.assertGreater(event.risk_score, 0)

    def test_is_known_device_after_session_created(self):
        fp = compute_device_fingerprint("Chrome", "Windows", UA_CHROME_DESKTOP)
        UserSession.objects.create(
            user=self.user,
            tenant=self.tenant,
            device_fingerprint=fp,
        )
        self.assertTrue(is_known_device(self.user, fp))


# ─────────────────────────────────────────────────────────────────────────────
# 10. Login failed: success=False, risk_score > 0
# ─────────────────────────────────────────────────────────────────────────────

class LoginFailedTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = make_tenant()
        self.user = make_user(tenant=self.tenant)

    def test_failed_login_success_false(self):
        event = log_auth_event("login_failed", user=self.user, success=False)
        self.assertFalse(event.success)

    def test_failed_login_risk_score_positive(self):
        event = log_auth_event("login_failed", user=self.user, success=False)
        self.assertGreater(event.risk_score, 0)

    def test_failed_login_no_user_stores_email_in_metadata(self):
        event = log_auth_event(
            "login_failed",
            success=False,
            metadata={"email": "unknown@example.com", "error": "Invalid credentials"},
        )
        self.assertIsNone(event.user)
        self.assertEqual(event.metadata["email"], "unknown@example.com")
        self.assertEqual(event.metadata["error"], "Invalid credentials")

    def test_multiple_failed_logins_all_stored(self):
        for i in range(5):
            log_auth_event("login_failed", user=self.user, success=False)
        self.assertEqual(
            AuthEvent.objects.filter(user=self.user, event_type="login_failed").count(),
            5,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 11. ActiveSessionsView tests
# ─────────────────────────────────────────────────────────────────────────────

class ActiveSessionsViewTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = make_tenant()
        self.user = make_user(tenant=self.tenant, email="sessions_user@example.com")

    def _login(self):
        response = self.client.post(
            LOGIN_URL,
            {"email": "sessions_user@example.com", "password": "Secure!Pass99"},
            format="json",
        )
        return response

    def test_get_sessions_requires_auth(self):
        response = self.client.get(SESSIONS_URL)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_returns_only_active_sessions(self):
        self.client.force_authenticate(user=self.user)
        # Create active sessions
        UserSession.objects.create(user=self.user, tenant=self.tenant)
        UserSession.objects.create(user=self.user, tenant=self.tenant)
        # Create a revoked session
        UserSession.objects.create(
            user=self.user, tenant=self.tenant, revoked_at=timezone.now()
        )

        response = self.client.get(SESSIONS_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_get_excludes_old_sessions(self):
        self.client.force_authenticate(user=self.user)
        # Create an old session (>30 days)
        session = UserSession.objects.create(user=self.user, tenant=self.tenant)
        old_time = timezone.now() - timedelta(days=31)
        UserSession.objects.filter(pk=session.pk).update(login_at=old_time)

        response = self.client.get(SESSIONS_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

    def test_get_session_fields(self):
        self.client.force_authenticate(user=self.user)
        UserSession.objects.create(
            user=self.user,
            tenant=self.tenant,
            browser="Chrome",
            os="Windows",
            device_type="desktop",
            ip_address="1.2.3.4",
        )

        response = self.client.get(SESSIONS_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        session_data = response.data[0]
        self.assertIn("id", session_data)
        self.assertIn("browser", session_data)
        self.assertIn("os", session_data)
        self.assertIn("device_type", session_data)
        self.assertIn("login_at", session_data)
        self.assertIn("last_seen_at", session_data)
        self.assertIn("is_current", session_data)

    def test_delete_specific_session(self):
        self.client.force_authenticate(user=self.user)
        session = UserSession.objects.create(user=self.user, tenant=self.tenant)

        url = f"{SESSIONS_URL}{session.pk}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["revoked"], 1)

        session.refresh_from_db()
        self.assertIsNotNone(session.revoked_at)

    def test_delete_specific_session_not_found(self):
        self.client.force_authenticate(user=self.user)
        import uuid
        fake_id = uuid.uuid4()
        url = f"{SESSIONS_URL}{fake_id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_all_sessions_revokes_others(self):
        self.client.force_authenticate(user=self.user)
        s1 = UserSession.objects.create(user=self.user, tenant=self.tenant)
        s2 = UserSession.objects.create(user=self.user, tenant=self.tenant)
        s3 = UserSession.objects.create(user=self.user, tenant=self.tenant)

        response = self.client.delete(SESSIONS_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["revoked"], 3)

        for s in [s1, s2, s3]:
            s.refresh_from_db()
            self.assertIsNotNone(s.revoked_at)

    def test_cannot_see_other_users_sessions(self):
        other_user = make_user(tenant=self.tenant, email="other2@example.com")
        UserSession.objects.create(user=other_user, tenant=self.tenant)

        self.client.force_authenticate(user=self.user)
        response = self.client.get(SESSIONS_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

    def test_delete_other_users_session_returns_404(self):
        other_user = make_user(tenant=self.tenant, email="other3@example.com")
        session = UserSession.objects.create(user=other_user, tenant=self.tenant)

        self.client.force_authenticate(user=self.user)
        url = f"{SESSIONS_URL}{session.pk}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ─────────────────────────────────────────────────────────────────────────────
# 12. AuthEventListView tests
# ─────────────────────────────────────────────────────────────────────────────

class AuthEventListViewTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = make_tenant()
        self.user = make_user(tenant=self.tenant, email="events_user@example.com")
        self.other_user = make_user(tenant=self.tenant, email="other_events@example.com")

    def test_requires_authentication(self):
        response = self.client.get(AUTH_EVENTS_URL)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_returns_own_events_only(self):
        self.client.force_authenticate(user=self.user)
        # Create events for both users
        log_auth_event("login_success", user=self.user)
        log_auth_event("login_success", user=self.user)
        log_auth_event("login_success", user=self.other_user)

        response = self.client.get(AUTH_EVENTS_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_returns_correct_fields(self):
        self.client.force_authenticate(user=self.user)
        log_auth_event("login_success", user=self.user)

        response = self.client.get(AUTH_EVENTS_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = response.data[0]
        self.assertIn("id", event)
        self.assertIn("event_type", event)
        self.assertIn("success", event)
        self.assertIn("risk_score", event)
        self.assertIn("created_at", event)

    def test_excludes_events_older_than_90_days(self):
        self.client.force_authenticate(user=self.user)
        event = AuthEvent.objects.create(
            user=self.user,
            tenant=self.tenant,
            event_type="login_success",
        )
        # Simulate old event
        old_time = timezone.now() - timedelta(days=91)
        AuthEvent.objects.filter(pk=event.pk).update(created_at=old_time)

        response = self.client.get(AUTH_EVENTS_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

    def test_returns_at_most_50_events(self):
        self.client.force_authenticate(user=self.user)
        for _ in range(60):
            AuthEvent.objects.create(
                user=self.user,
                tenant=self.tenant,
                event_type="login_success",
            )

        response = self.client.get(AUTH_EVENTS_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertLessEqual(len(response.data), 50)

    def test_ordered_by_newest_first(self):
        self.client.force_authenticate(user=self.user)
        e1 = AuthEvent.objects.create(user=self.user, tenant=self.tenant, event_type="login_success")
        e2 = AuthEvent.objects.create(user=self.user, tenant=self.tenant, event_type="logout")

        response = self.client.get(AUTH_EVENTS_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Newest first: e2 should come before e1
        ids = [str(e["id"]) for e in response.data]
        self.assertGreater(ids.index(str(e1.pk)), ids.index(str(e2.pk)))


# ─────────────────────────────────────────────────────────────────────────────
# 13. Migration validation
# ─────────────────────────────────────────────────────────────────────────────

class MigrationValidationTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_migration_module_importable(self):
        import importlib
        mod = importlib.import_module("apps.telemetry.migrations.0001_initial")
        self.assertTrue(hasattr(mod, "Migration"))

    def test_migration_has_initial_true(self):
        import importlib
        mod = importlib.import_module("apps.telemetry.migrations.0001_initial")
        self.assertTrue(mod.Migration.initial)

    def test_migration_has_correct_dependencies(self):
        import importlib
        mod = importlib.import_module("apps.telemetry.migrations.0001_initial")
        deps = mod.Migration.dependencies
        dep_names = [(app, name) for app, name in deps]
        self.assertIn(("tenants", "0004_tenant_plan_free"), dep_names)
        self.assertIn(
            ("users", "0002_user_email_verified_emailverificationtoken_passwordresettoken"),
            dep_names,
        )

    def test_migration_has_operations(self):
        import importlib
        mod = importlib.import_module("apps.telemetry.migrations.0001_initial")
        self.assertTrue(len(mod.Migration.operations) > 0)

    def test_migration_creates_usersession(self):
        import importlib
        from django.db.migrations import CreateModel
        mod = importlib.import_module("apps.telemetry.migrations.0001_initial")
        model_names = [
            op.name for op in mod.Migration.operations if isinstance(op, CreateModel)
        ]
        self.assertIn("UserSession", model_names)

    def test_migration_creates_authevent(self):
        import importlib
        from django.db.migrations import CreateModel
        mod = importlib.import_module("apps.telemetry.migrations.0001_initial")
        model_names = [
            op.name for op in mod.Migration.operations if isinstance(op, CreateModel)
        ]
        self.assertIn("AuthEvent", model_names)

    def test_migration_adds_indexes(self):
        import importlib
        from django.db.migrations import AddIndex
        mod = importlib.import_module("apps.telemetry.migrations.0001_initial")
        index_ops = [op for op in mod.Migration.operations if isinstance(op, AddIndex)]
        # 3 for UserSession + 5 for AuthEvent
        self.assertGreaterEqual(len(index_ops), 8)
