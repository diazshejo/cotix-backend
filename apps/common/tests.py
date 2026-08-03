from pathlib import Path

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from apps.common.models import SecurityEvent
from apps.common.logging import RedactSensitiveFilter
from apps.tenants.models import Tenant


class ProductionSettingsTests(SimpleTestCase):
    @override_settings(DEBUG=False)
    def test_debug_can_be_disabled(self):
        self.assertFalse(settings.DEBUG)

    def test_cors_is_not_open_by_default(self):
        self.assertFalse(settings.CORS_ALLOW_ALL_ORIGINS)

    def test_settings_do_not_include_known_database_secret(self):
        backend_dir = Path(__file__).resolve().parents[2]
        settings_text = (backend_dir / "config" / "settings.py").read_text(encoding="utf-8")
        env_example_text = (backend_dir / ".env.example").read_text(encoding="utf-8")
        self.assertNotIn('DB_PASSWORD", default="Hyper', settings_text)
        self.assertNotIn("DB_PASSWORD=Hyper", env_example_text)


class AdminHardeningTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.tenant = Tenant.objects.create(slug="admin-tenant", business_name="Admin Tenant", email="admin@example.com")
        self.admin_user = get_user_model().objects.create_superuser(
            email="root@example.com",
            password="StrongPass123",
        )

    def test_security_event_admin_is_readonly(self):
        event_admin = admin.site._registry[SecurityEvent]
        request = self.factory.get("/admin/common/securityevent/")
        request.user = self.admin_user
        event = SecurityEvent.objects.create(tenant=self.tenant, event_type="login_failed", severity="warning")

        self.assertFalse(event_admin.has_add_permission(request))
        self.assertFalse(event_admin.has_change_permission(request, event))
        self.assertFalse(event_admin.has_delete_permission(request, event))


class LoggingRedactionTests(SimpleTestCase):
    def test_redacts_authorization_and_api_keys(self):
        import logging

        record = logging.LogRecord(
            name="cotix.security",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Authorization: Bearer secret-token api-key: super-secret password=hidden",
            args=(),
            exc_info=None,
        )

        RedactSensitiveFilter().filter(record)

        self.assertNotIn("secret-token", record.msg)
        self.assertNotIn("super-secret", record.msg)
        self.assertNotIn("hidden", record.msg)
