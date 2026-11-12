import secrets
import threading
from decimal import Decimal

from django.core.cache import cache
from django.db import close_old_connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.catalog.models import CatalogItem
from apps.clients.models import Client
from apps.common.models import SecurityEvent
from apps.telemetry.models import AuthEvent
from apps.quotes.models import Quote, QuoteItem
from apps.quotes.services import next_quote_number, recalculate_quote
from apps.tenants.models import Tenant
from apps.tenants.services.plans import PLAN_BASIC, PLAN_BUSINESS, PLAN_FREE, PLAN_PREMIUM
from apps.users.models import User
from apps.users.security import LOGIN_LOCKOUT_MESSAGE


class ValidationHardeningTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(slug="tenant-a", business_name="Tenant A", email="a@example.com")
        self.other_tenant = Tenant.objects.create(slug="tenant-b", business_name="Tenant B", email="b@example.com")
        self.user = User.objects.create_user(
            email="admin-a@example.com",
            password="StrongPass123",
            role=User.Role.TENANT_ADMIN,
            tenant=self.tenant,
            email_verified=True,
        )
        self.other_user = User.objects.create_user(
            email="admin-b@example.com",
            password="StrongPass123",
            role=User.Role.TENANT_ADMIN,
            tenant=self.other_tenant,
            email_verified=True,
        )
        self.seller = User.objects.create_user(
            email="seller-a@example.com",
            password="StrongPass123",
            role=User.Role.SELLER,
            tenant=self.tenant,
        )
        self.viewer = User.objects.create_user(
            email="viewer-a@example.com",
            password="StrongPass123",
            role=User.Role.VIEWER,
            tenant=self.tenant,
        )
        self.client.force_authenticate(self.user)
        self.client_obj = Client.objects.create(tenant=self.tenant, name="Cliente A", email="cliente@example.com")
        self.item = CatalogItem.objects.create(
            tenant=self.tenant,
            name="Servicio",
            description="Servicio profesional",
            unit_price=Decimal("100.0000"),
            unit="hora",
            currency="GTQ",
            taxable=True,
        )

    def create_quote(self, status=Quote.Status.DRAFT, expiry_date=None, client_email="cliente@example.com"):
        self.client_obj.email = client_email
        self.client_obj.save(update_fields=["email", "updated_at"])
        quote = Quote.objects.create(
            tenant=self.tenant,
            quote_number=next_quote_number(self.tenant),
            client=self.client_obj,
            created_by=self.user,
            title="Cotizacion prueba",
            issue_date=timezone.localdate(),
            expiry_date=expiry_date,
            currency="GTQ",
            tax_rate_snapshot=self.tenant.tax_rate,
            status=status,
        )
        QuoteItem.objects.create(
            quote=quote,
            catalog_item=self.item,
            description=self.item.description,
            quantity=Decimal("1.0000"),
            unit=self.item.unit,
            unit_price=self.item.unit_price,
            taxable=True,
        )
        recalculate_quote(quote)
        return quote

    def test_no_duplicate_client_email_same_tenant(self):
        response = self.client.post("/api/clients/", {"name": "Otro", "email": "cliente@example.com"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.data)

    def test_same_client_email_allowed_in_different_tenant(self):
        self.client.force_authenticate(self.other_user)
        response = self.client.post("/api/clients/", {"name": "Otro", "email": "cliente@example.com"}, format="json")
        self.assertEqual(response.status_code, 201)

    def test_no_catalog_item_with_negative_price(self):
        response = self.client.post(
            "/api/catalog/items/",
            {"name": "Malo", "unit": "unidad", "unit_price": "-1.0000", "currency": "GTQ", "item_type": "service"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("unit_price", response.data)

    def test_no_quote_without_lines(self):
        response = self.client.post(
            "/api/quotes/",
            {"client": str(self.client_obj.id), "title": "Sin lineas", "issue_date": str(timezone.localdate()), "items": []},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("detail", response.data)

    def test_no_edit_accepted_quote(self):
        quote = self.create_quote(status=Quote.Status.ACCEPTED)
        response = self.client.patch(f"/api/quotes/{quote.id}/", {"title": "No permitido"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_no_send_quote_without_client_email(self):
        quote = self.create_quote(client_email="")
        response = self.client.post(f"/api/quotes/{quote.id}/send-email/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "No puedes enviar por email una cotizacion si el cliente no tiene email.")

    def test_no_cross_tenant_access(self):
        other_client = Client.objects.create(tenant=self.other_tenant, name="Cliente B")
        response = self.client.get(f"/api/clients/{other_client.id}/")
        self.assertEqual(response.status_code, 404)

    def test_public_quote_hides_internal_notes(self):
        quote = self.create_quote(status=Quote.Status.SENT)
        quote.internal_notes = "Margen interno secreto"
        quote.save(update_fields=["internal_notes", "updated_at"])
        response = self.client.get(f"/api/public/quotes/{quote.public_token}/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("internal_notes", response.data)
        self.assertNotIn("assigned_to", response.data)
        self.assertNotIn("client", response.data)
        self.assertNotIn("public_token", response.data)
        self.assertNotIn("catalog_item", response.data["items"][0])

    def test_draft_requires_publication_before_client_can_view(self):
        quote = self.create_quote()
        self.assertEqual(self.client.get(f"/api/public/quotes/{quote.public_token}/").status_code, 403)
        response = self.client.post(f"/api/quotes/{quote.id}/publish/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], Quote.Status.SENT)
        self.assertEqual(self.client.get(f"/api/public/quotes/{quote.public_token}/").status_code, 200)

    def test_dashboard_period_excludes_older_quotes(self):
        current = self.create_quote(status=Quote.Status.ACCEPTED)
        older = self.create_quote(status=Quote.Status.ACCEPTED)
        older.issue_date = timezone.localdate().replace(year=timezone.localdate().year - 1)
        older.save(update_fields=["issue_date", "updated_at"])
        response = self.client.get("/api/reports/dashboard/?period=month&currency=GTQ")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["quotes_created"], 1)
        self.assertEqual(Decimal(str(response.data["total_quoted"])), current.total)
        self.assertEqual(self.client.get("/api/reports/dashboard/?period=invalid").status_code, 400)

    def test_no_accept_expired_quote(self):
        quote = self.create_quote(status=Quote.Status.SENT, expiry_date=timezone.localdate() - timezone.timedelta(days=1))
        response = self.client.post(f"/api/public/quotes/{quote.public_token}/respond/", {"response": "accepted"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "Esta cotizacion ya vencio y no puede responderse.")

    def test_no_accept_already_answered_quote(self):
        quote = self.create_quote(status=Quote.Status.ACCEPTED)
        response = self.client.post(f"/api/public/quotes/{quote.public_token}/respond/", {"response": "rejected"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "Esta cotizacion ya fue respondida.")

    def test_tenant_admin_can_manage_users(self):
        self.tenant.plan = PLAN_BUSINESS
        self.tenant.save(update_fields=["plan"])
        response = self.client.post(
            "/api/auth/users/",
            {"email": "new-seller@example.com", "password": "StrongPass123", "role": User.Role.SELLER},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_seller_cannot_manage_users(self):
        self.client.force_authenticate(self.seller)
        response = self.client.post(
            "/api/auth/users/",
            {"email": "blocked@example.com", "password": "StrongPass123", "role": User.Role.SELLER},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_viewer_cannot_create_client(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.post("/api/clients/", {"name": "No permitido"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_viewer_cannot_create_quote(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.post(
            "/api/quotes/",
            {"client": str(self.client_obj.id), "title": "No", "issue_date": str(timezone.localdate()), "items": []},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_seller_cannot_change_critical_tenant_settings(self):
        self.client.force_authenticate(self.seller)
        response = self.client.patch("/api/tenant/settings/", {"tax_rate": "15.00"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_login_throttle_works(self):
        self.client.force_authenticate(None)
        for _ in range(5):
            response = self.client.post("/api/auth/login/", {"email": self.user.email, "password": "bad"}, format="json")
            self.assertEqual(response.status_code, 401)
        response = self.client.post("/api/auth/login/", {"email": self.user.email, "password": "bad"}, format="json")
        self.assertEqual(response.status_code, 429)

    def test_send_email_throttle_works(self):
        quote = self.create_quote(status=Quote.Status.DRAFT)
        for _ in range(10):
            response = self.client.post(f"/api/quotes/{quote.id}/send-email/", {}, format="json")
            self.assertEqual(response.status_code, 200)
        response = self.client.post(f"/api/quotes/{quote.id}/send-email/", {}, format="json")
        self.assertEqual(response.status_code, 429)

    def test_security_event_login_success_created(self):
        self.client.force_authenticate(None)
        response = self.client.post("/api/auth/login/", {"email": self.user.email, "password": "StrongPass123"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(AuthEvent.objects.filter(event_type=AuthEvent.EventType.LOGIN_SUCCESS, user=self.user).exists())

    def test_security_event_login_failed_created(self):
        self.client.force_authenticate(None)
        response = self.client.post("/api/auth/login/", {"email": self.user.email, "password": "bad"}, format="json")
        self.assertEqual(response.status_code, 401)
        self.assertTrue(AuthEvent.objects.filter(event_type=AuthEvent.EventType.LOGIN_FAILED).exists())

    def test_login_blocked_after_failed_attempts(self):
        self.client.force_authenticate(None)
        for index in range(5):
            response = self.client.post(
                "/api/auth/login/",
                {"email": self.user.email, "password": "bad"},
                format="json",
                HTTP_X_FORWARDED_FOR=f"10.10.0.{index}",
            )
            self.assertEqual(response.status_code, 401)

        response = self.client.post(
            "/api/auth/login/",
            {"email": self.user.email, "password": "bad"},
            format="json",
            HTTP_X_FORWARDED_FOR="10.10.0.99",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], LOGIN_LOCKOUT_MESSAGE)
        self.assertTrue(AuthEvent.objects.filter(event_type=AuthEvent.EventType.LOGIN_BLOCKED).exists())

    def test_login_success_resets_failed_attempt_window(self):
        self.client.force_authenticate(None)
        for index in range(4):
            response = self.client.post(
                "/api/auth/login/",
                {"email": self.user.email, "password": "bad"},
                format="json",
                HTTP_X_FORWARDED_FOR=f"10.20.0.{index}",
            )
            self.assertEqual(response.status_code, 401)

        response = self.client.post(
            "/api/auth/login/",
            {"email": self.user.email, "password": "StrongPass123"},
            format="json",
            HTTP_X_FORWARDED_FOR="10.20.0.10",
        )
        self.assertEqual(response.status_code, 200)

        for index in range(4):
            response = self.client.post(
                "/api/auth/login/",
                {"email": self.user.email, "password": "bad"},
                format="json",
                HTTP_X_FORWARDED_FOR=f"10.20.1.{index}",
            )
            self.assertEqual(response.status_code, 401)

        response = self.client.post(
            "/api/auth/login/",
            {"email": self.user.email, "password": "bad"},
            format="json",
            HTTP_X_FORWARDED_FOR="10.20.1.99",
        )
        self.assertEqual(response.status_code, 401)

    def test_security_event_public_responded_created(self):
        quote = self.create_quote(status=Quote.Status.SENT)
        response = self.client.post(f"/api/public/quotes/{quote.public_token}/respond/", {
            "response": "accepted", "signer_name": "Cliente Firmante", "consent": True,
            "signature_strokes": [[{"x": index / 10, "y": (index % 3) / 3} for index in range(10)]],
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(SecurityEvent.objects.filter(event_type="quote_public_responded", tenant=self.tenant).exists())

    def test_quote_numbering_does_not_duplicate_sequentially(self):
        numbers = []
        for _ in range(5):
            quote = self.create_quote()
            numbers.append(quote.quote_number)
        self.assertEqual(len(numbers), len(set(numbers)))


def _make_tenant(slug, plan=PLAN_BASIC):
    return Tenant.objects.create(slug=slug, business_name=slug, email=f"{slug}@test.com", plan=plan)


def _make_user(tenant, email=None, role=User.Role.TENANT_ADMIN):
    email = email or f"admin-{tenant.slug}@test.com"
    return User.objects.create_user(email=email, password="pass123", role=role, tenant=tenant, is_active=True)


def _make_client(tenant):
    return Client.objects.create(tenant=tenant, name="Cliente Test", email="cliente@test.com")


def _bulk_quotes(tenant, client, user, count):
    """Inserta `count` cotizaciones directamente en BD para simular uso mensual."""
    Quote.objects.bulk_create([
        Quote(
            tenant=tenant,
            quote_number=f"BULK-{i:06d}",
            client=client,
            created_by=user,
            title=f"Cotizacion Bulk {i}",
            issue_date=timezone.localdate(),
            currency="GTQ",
            tax_rate_snapshot=Decimal("12.00"),
            status=Quote.Status.DRAFT,
            public_token=secrets.token_hex(64),
        )
        for i in range(1, count + 1)
    ])


VALID_QUOTE_PAYLOAD = {
    "title": "Test Plan Quota",
    "issue_date": None,  # se rellena en cada test
    "items": [
        {
            "description": "Servicio de prueba",
            "quantity": "1.0000",
            "unit": "hora",
            "unit_price": "100.0000",
            "taxable": True,
        }
    ],
}


class PlanQuotaTests(APITestCase):
    """Flujo end-to-end: límites de cotizaciones y usuarios por plan."""

    def setUp(self):
        cache.clear()

    def _payload(self, client_id):
        return {**VALID_QUOTE_PAYLOAD, "client": str(client_id), "issue_date": str(timezone.localdate())}

    # ── cotizaciones ──────────────────────────────────────────────────────────

    def test_basic_plan_allows_50th_quote(self):
        tenant = _make_tenant("basic-allow")
        user = _make_user(tenant)
        client = _make_client(tenant)
        _bulk_quotes(tenant, client, user, 49)  # 49 previas → la 50ª debe pasar

        self.client.force_authenticate(user)
        response = self.client.post("/api/quotes/", self._payload(client.id), format="json")
        self.assertEqual(response.status_code, 201)

    def test_basic_plan_blocks_51st_quote(self):
        tenant = _make_tenant("basic-block")
        user = _make_user(tenant)
        client = _make_client(tenant)
        _bulk_quotes(tenant, client, user, 50)  # 50 previas → la 51ª es 403

        self.client.force_authenticate(user)
        response = self.client.post("/api/quotes/", self._payload(client.id), format="json")

        self.assertEqual(response.status_code, 403)
        self.assertIn("50", response.data["detail"])
        self.assertIn("cotizaciones", response.data["detail"].lower())

    def test_basic_403_detail_triggers_isPlanRestriction_regex(self):
        """El mensaje del 403 debe hacer match con el regex del frontend."""
        import re
        tenant = _make_tenant("basic-regex")
        user = _make_user(tenant)
        client = _make_client(tenant)
        _bulk_quotes(tenant, client, user, 50)

        self.client.force_authenticate(user)
        response = self.client.post("/api/quotes/", self._payload(client.id), format="json")

        detail = response.data["detail"]
        # mismo regex que isPlanRestriction en client.ts
        pattern = re.compile(r"plan|premium|business|actualiza|integraci|cotizaci|usuario", re.IGNORECASE)
        self.assertTrue(pattern.search(detail), f"Regex no matchea: {detail!r}")

    def test_free_trial_uses_basic_monthly_quote_limit(self):
        tenant = _make_tenant("free-basic-limit", plan=PLAN_FREE)
        user = _make_user(tenant)
        client = _make_client(tenant)
        _bulk_quotes(tenant, client, user, 50)

        self.client.force_authenticate(user)
        response = self.client.post("/api/quotes/", self._payload(client.id), format="json")
        self.assertEqual(response.status_code, 403)
        self.assertIn("50", response.data["detail"])

    def test_premium_plan_has_no_monthly_quote_limit(self):
        tenant = _make_tenant("premium-nolimit", plan=PLAN_PREMIUM)
        user = _make_user(tenant)
        client = _make_client(tenant)
        _bulk_quotes(tenant, client, user, 200)

        self.client.force_authenticate(user)
        response = self.client.post("/api/quotes/", self._payload(client.id), format="json")
        self.assertEqual(response.status_code, 201)

    def test_super_admin_bypasses_quota(self):
        tenant = _make_tenant("basic-super")
        admin = _make_user(tenant)
        client = _make_client(tenant)
        _bulk_quotes(tenant, client, admin, 50)

        super_admin = User.objects.create_user(
            email="super@test.com", password="pass123",
            role=User.Role.SUPER_ADMIN, is_active=True,
        )
        self.client.force_authenticate(super_admin)
        # super_admin no tiene tenant → no aplica quota
        # (tenant_admin tiene quota, super_admin no)
        response = self.client.post("/api/quotes/", self._payload(client.id), format="json")
        # super_admin puede llegar al serializer (aunque falle por tenant mismatch,
        # lo importante es que NO sea 403 de quota)
        self.assertNotEqual(response.status_code, 403)

    # ── plan-usage endpoint ───────────────────────────────────────────────────

    def test_plan_usage_returns_used_and_limit_for_basic(self):
        tenant = _make_tenant("basic-usage")
        user = _make_user(tenant)
        client = _make_client(tenant)
        _bulk_quotes(tenant, client, user, 15)

        self.client.force_authenticate(user)
        response = self.client.get("/api/quotes/plan-usage/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["monthly_quotes"]["used"], 15)
        self.assertEqual(response.data["monthly_quotes"]["limit"], 50)

    def test_plan_usage_returns_null_limit_for_premium(self):
        tenant = _make_tenant("premium-usage", plan=PLAN_PREMIUM)
        user = _make_user(tenant)

        self.client.force_authenticate(user)
        response = self.client.get("/api/quotes/plan-usage/")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["monthly_quotes"]["limit"])

    def test_plan_usage_requires_authentication(self):
        response = self.client.get("/api/quotes/plan-usage/")
        self.assertEqual(response.status_code, 401)

    # ── usuarios ──────────────────────────────────────────────────────────────

    def test_basic_plan_blocks_second_user(self):
        # plan basic: max_users=1. Ya hay 1 (el admin). El 2do debe ser 403.
        tenant = _make_tenant("basic-users")
        user = _make_user(tenant)  # usuario 1

        self.client.force_authenticate(user)
        response = self.client.post(
            "/api/auth/users/",
            {"email": "seller2@test.com", "password": "StrongPass123", "role": User.Role.SELLER},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("1", response.data["detail"])
        self.assertIn("usuario", response.data["detail"].lower())

    def test_premium_plan_allows_3_users(self):
        tenant = _make_tenant("premium-users", plan=PLAN_PREMIUM)
        admin = _make_user(tenant)  # usuario 1
        _make_user(tenant, email="u2@test.com", role=User.Role.SELLER)  # usuario 2

        self.client.force_authenticate(admin)
        # crear usuario 3 → debe pasar
        response = self.client.post(
            "/api/auth/users/",
            {"email": "u3@test.com", "password": "StrongPass123", "role": User.Role.SELLER},
            format="json",
        )
        self.assertEqual(response.status_code, 201)

    def test_premium_plan_blocks_4th_user(self):
        tenant = _make_tenant("premium-users-block", plan=PLAN_PREMIUM)
        admin = _make_user(tenant)
        _make_user(tenant, email="u2@premium.com", role=User.Role.SELLER)
        _make_user(tenant, email="u3@premium.com", role=User.Role.VIEWER)
        # ya hay 3 usuarios, el 4to debe ser 403

        self.client.force_authenticate(admin)
        response = self.client.post(
            "/api/auth/users/",
            {"email": "u4@premium.com", "password": "StrongPass123", "role": User.Role.SELLER},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("3", response.data["detail"])


class QuoteNumberingConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(slug="tenant-concurrent", business_name="Tenant Concurrent", email="c@example.com")
        self.user = User.objects.create_user(
            email="admin-concurrent@example.com",
            password="StrongPass123",
            role=User.Role.TENANT_ADMIN,
            tenant=self.tenant,
        )
        self.client_obj = Client.objects.create(tenant=self.tenant, name="Cliente Concurrente", email="c@example.com")

    def test_quote_numbering_is_unique_under_concurrency(self):
        numbers = []
        errors = []
        lock = threading.Lock()

        def create_quote():
            close_old_connections()
            try:
                with transaction.atomic():
                    tenant = Tenant.objects.get(pk=self.tenant.pk)
                    number = next_quote_number(tenant)
                    Quote.objects.create(
                        tenant=tenant,
                        quote_number=number,
                        client=Client.objects.get(pk=self.client_obj.pk),
                        created_by=User.objects.get(pk=self.user.pk),
                        title="Concurrente",
                        issue_date=timezone.localdate(),
                        currency="GTQ",
                        tax_rate_snapshot=tenant.tax_rate,
                    )
                with lock:
                    numbers.append(number)
            except Exception as exc:
                with lock:
                    errors.append(str(exc))
            finally:
                close_old_connections()

        threads = [threading.Thread(target=create_quote) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(numbers), 5)
        self.assertEqual(len(set(numbers)), 5)
        digits = self.tenant.quote_digits
        self.assertEqual(sorted(numbers), [f"COT-{timezone.now().year}-{i:0{digits}d}" for i in range(1, 6)])
