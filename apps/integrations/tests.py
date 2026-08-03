from io import BytesIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.catalog.models import CatalogItem
from apps.integrations.models import IntegrationSyncLog, TenantCatalogIntegration
from apps.integrations.serializers import TenantCatalogIntegrationSerializer
from apps.integrations.services.crypto import decrypt_text, encrypt_text
from apps.integrations.services.fetchers import CSVFetcher, RestAPIFetcher, SafeRedirectHandler
from apps.integrations.services.mappers import apply_filters, extract_array_from_response, map_record, suggest_mapping
from apps.integrations.services.redaction import sanitize_error_message
from apps.integrations.services.sync import CatalogSyncEngine
from apps.integrations.services.validators import validate_external_url
from apps.tenants.models import Tenant
from apps.tenants.services.plans import FEATURE_API_INTEGRATIONS
from apps.users.models import User


def make_tenant(slug="tenant-integrations", plan="premium"):
    return Tenant.objects.create(slug=slug, business_name=slug, email=f"{slug}@example.com", plan=plan)


def make_user(tenant):
    return User.objects.create_user(
        email=f"admin-{tenant.slug}@example.com",
        password="password123",
        role=User.Role.TENANT_ADMIN,
        tenant=tenant,
        is_active=True,
    )


def make_integration(tenant, user, **overrides):
    data = {
        "tenant": tenant,
        "created_by": user,
        "name": "ERP",
        "integration_type": "rest_api",
        "endpoint_url": "https://api.example.com/products",
        "auth_type": "none",
        "field_mapping": {"sku": "codigo", "name": "descripcion", "unit_price": "precio", "unit": "unidad"},
        "response_array_path": "data.items",
    }
    data.update(overrides)
    return TenantCatalogIntegration.objects.create(**data)


class MappingTests(TestCase):
    def test_extract_array_from_nested_response_path(self):
        response = {"data": {"items": [{"codigo": "A1"}]}}
        self.assertEqual(extract_array_from_response(response, "data.items"), [{"codigo": "A1"}])

    def test_mapping_and_suggestion(self):
        records = [{"codigo": "A1", "descripcion": "Cemento", "precio": "10.50", "unidad": "saco"}]
        self.assertEqual(suggest_mapping(records)["sku"], "codigo")
        mapped = map_record(records[0], {"sku": "codigo", "name": "descripcion", "unit_price": "precio", "unit": "unidad"})
        self.assertEqual(mapped["external_id"], "A1")
        self.assertEqual(str(mapped["unit_price"]), "10.5000")

    def test_filters(self):
        records = [{"sku": "A", "activo": True, "precio": "10"}, {"sku": "B", "activo": False, "precio": "20"}]
        filtered = apply_filters(records, [{"field": "activo", "operator": "eq", "value": "true"}])
        self.assertEqual([row["sku"] for row in filtered], ["A"])


class SecurityAndFetcherTests(TestCase):
    def setUp(self):
        self.tenant = make_tenant("tenant-fetcher")
        self.user = make_user(self.tenant)

    def test_ssrf_validation_rejects_private_urls(self):
        for url in ("http://127.0.0.1:8000/products", "http://169.254.169.254/latest/meta-data", "https://localhost/products"):
            with self.subTest(url=url):
                with self.assertRaises(Exception):
                    validate_external_url(url)

    def test_ssrf_validation_rejects_redirects_to_private_urls(self):
        handler = SafeRedirectHandler()

        with self.assertRaises(Exception):
            handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1:8000/private")

    def test_auth_headers_for_supported_modes(self):
        api_key = make_integration(
            self.tenant,
            self.user,
            auth_type="api_key",
            auth_header_name="X-API-Key",
            credentials_enc=encrypt_text("secret-key"),
        )
        self.assertEqual(RestAPIFetcher(api_key).build_headers()["X-API-Key"], "secret-key")

        bearer = make_integration(
            self.tenant,
            self.user,
            name="Bearer",
            auth_type="bearer",
            credentials_enc=encrypt_text("token-123"),
        )
        self.assertEqual(RestAPIFetcher(bearer).build_headers()["Authorization"], "Bearer token-123")

        basic = make_integration(
            self.tenant,
            self.user,
            name="Basic",
            auth_type="basic",
            credentials_enc=encrypt_text("user:pass"),
        )
        self.assertTrue(RestAPIFetcher(basic).build_headers()["Authorization"].startswith("Basic "))

    def test_credentials_are_encrypted_at_rest(self):
        encrypted = encrypt_text("plain-secret")
        self.assertNotIn("plain-secret", encrypted)
        self.assertEqual(decrypt_text(encrypted), "plain-secret")

    def test_serializer_never_exposes_encrypted_secret_fields(self):
        integration = make_integration(
            self.tenant,
            self.user,
            credentials_enc=encrypt_text("secret-key"),
            custom_headers_enc=encrypt_text("header-secret"),
        )
        data = TenantCatalogIntegrationSerializer(integration).data
        self.assertNotIn("credentials_enc", data)
        self.assertNotIn("custom_headers_enc", data)
        self.assertNotIn("connection_string_enc", data)
        self.assertNotIn("secret-key", str(data))

    def test_visible_errors_are_redacted(self):
        message = sanitize_error_message("Authorization: Bearer secret-token password=hidden")
        self.assertNotIn("secret-token", message)
        self.assertNotIn("hidden", message)

    def test_csv_upload_rejects_non_csv_extension(self):
        uploaded = BytesIO(b"codigo,descripcion\nA1,Arena\n")
        uploaded.name = "catalog.xlsx"
        uploaded.size = len(uploaded.getvalue())
        uploaded.content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

        with self.assertRaises(Exception):
            CSVFetcher().fetch_from_upload(uploaded)

    def test_csv_upload_rejects_too_many_rows(self):
        uploaded = BytesIO(("codigo,descripcion\n" + "\n".join(f"A{i},Arena" for i in range(5001))).encode("utf-8"))
        uploaded.name = "catalog.csv"
        uploaded.size = len(uploaded.getvalue())
        uploaded.content_type = "text/csv"

        with self.assertRaises(Exception):
            CSVFetcher().fetch_from_upload(uploaded)

    def test_csv_upload_rejects_invalid_encoding(self):
        uploaded = BytesIO(b"codigo,descripcion\nA1,\xff\n")
        uploaded.name = "catalog.csv"
        uploaded.size = len(uploaded.getvalue())
        uploaded.content_type = "text/csv"

        with self.assertRaises(Exception):
            CSVFetcher().fetch_from_upload(uploaded)


class SyncEngineTests(TestCase):
    def setUp(self):
        self.tenant = make_tenant("tenant-sync")
        self.user = make_user(self.tenant)
        self.integration = make_integration(self.tenant, self.user)

    def test_basic_sync_creates_updates_skips_and_deactivates_absent_products(self):
        CatalogSyncEngine(
            self.integration,
            triggered_by_user=self.user,
            records=[{"codigo": "A1", "descripcion": "Cemento", "precio": "10", "unidad": "saco"}],
        ).run()
        item = CatalogItem.objects.get(integration=self.integration, external_id="A1")
        self.assertEqual(item.name, "Cemento")
        self.assertTrue(item.is_synced)

        log = CatalogSyncEngine(
            self.integration,
            triggered_by_user=self.user,
            records=[{"codigo": "A1", "descripcion": "Cemento gris", "precio": "11", "unidad": "saco"}],
        ).run()
        item.refresh_from_db()
        self.assertEqual(item.name, "Cemento gris")
        self.assertEqual(str(item.unit_price), "11.0000")
        self.assertEqual(log.updated_count, 1)

        CatalogSyncEngine(self.integration, triggered_by_user=self.user, records=[]).run()
        item.refresh_from_db()
        self.assertFalse(item.is_active)

    def test_sync_never_deactivates_manual_products(self):
        manual = CatalogItem.objects.create(
            tenant=self.tenant,
            sku="MANUAL-1",
            name="Manual",
            unit_price="5.0000",
            is_active=True,
            is_synced=False,
        )
        CatalogSyncEngine(
            self.integration,
            triggered_by_user=self.user,
            records=[{"codigo": "A1", "descripcion": "Cemento", "precio": "10", "unidad": "saco"}],
        ).run()
        manual.refresh_from_db()
        self.assertTrue(manual.is_active)


class IntegrationAPITests(APITestCase):
    def setUp(self):
        self.tenant = make_tenant("tenant-api")
        self.user = make_user(self.tenant)
        self.client.force_authenticate(self.user)

    @patch("apps.integrations.views.RestAPIFetcher")
    def test_connection_endpoint_returns_preview_metadata(self, fetcher_cls):
        integration = make_integration(self.tenant, self.user)
        fetcher_cls.return_value.fetch_raw.return_value = {
            "data": {"items": [{"codigo": "A1", "descripcion": "Block", "precio": "4.5"}]}
        }

        response = self.client.post(f"/api/integrations/catalog/{integration.id}/test/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "success")
        self.assertEqual(response.data["products_found"], 1)
        self.assertEqual(response.data["suggested_mapping"]["sku"], "codigo")

    def test_basic_plan_cannot_create_or_activate_integrations(self):
        basic_tenant = make_tenant("tenant-basic", plan="basic")
        basic_user = make_user(basic_tenant)
        self.client.force_authenticate(basic_user)

        response = self.client.post(
            "/api/integrations/catalog/",
            {
                "name": "ERP",
                "integration_type": "rest_api",
                "endpoint_url": "https://api.example.com/products",
                "field_mapping": {"sku": "codigo", "name": "descripcion", "unit_price": "precio"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

        integration = make_integration(basic_tenant, basic_user, is_active=False)
        response = self.client.patch(f"/api/integrations/catalog/{integration.id}/", {"is_active": True}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertIn("Premium", str(response.data["detail"]))

    def test_free_plan_cannot_create_integrations(self):
        free_tenant = make_tenant("tenant-free", plan="free")
        free_user = make_user(free_tenant)
        self.client.force_authenticate(free_user)

        response = self.client.post(
            "/api/integrations/catalog/",
            {
                "name": "ERP",
                "integration_type": "rest_api",
                "endpoint_url": "https://api.example.com/products",
                "field_mapping": {"sku": "codigo", "name": "descripcion", "unit_price": "precio"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("Premium", str(response.data["detail"]))

    def test_premium_and_business_plans_can_create_integrations(self):
        for plan in ("premium", "business"):
            with self.subTest(plan=plan):
                tenant = make_tenant(f"tenant-{plan}", plan=plan)
                user = make_user(tenant)
                self.client.force_authenticate(user)

                response = self.client.post(
                    "/api/integrations/catalog/",
                    {
                        "name": f"ERP {plan}",
                        "integration_type": "rest_api",
                        "endpoint_url": "https://api.example.com/products",
                        "field_mapping": {"sku": "codigo", "name": "descripcion", "unit_price": "precio"},
                    },
                    format="json",
                )

                self.assertEqual(response.status_code, 201)

    def test_downgraded_tenant_cannot_use_active_integration_actions(self):
        integration = make_integration(self.tenant, self.user, is_active=True)
        self.tenant.plan = "basic"
        self.tenant.save(update_fields=["plan"])

        for method, path in (
            ("post", f"/api/integrations/catalog/{integration.id}/test/"),
            ("get", f"/api/integrations/catalog/{integration.id}/preview/"),
            ("post", f"/api/integrations/catalog/{integration.id}/sync/"),
            ("get", f"/api/integrations/catalog/{integration.id}/logs/"),
        ):
            with self.subTest(path=path):
                response = getattr(self.client, method)(path, {}, format="json")
                self.assertEqual(response.status_code, 403)

        response = self.client.patch(f"/api/integrations/catalog/{integration.id}/", {"name": "Nuevo ERP"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_downgraded_tenant_can_pause_active_integration(self):
        integration = make_integration(self.tenant, self.user, is_active=True)
        self.tenant.plan = "basic"
        self.tenant.save(update_fields=["plan"])

        response = self.client.patch(f"/api/integrations/catalog/{integration.id}/", {"is_active": False}, format="json")

        self.assertEqual(response.status_code, 200)
        integration.refresh_from_db()
        self.assertFalse(integration.is_active)

    def test_tenant_feature_helper_uses_plan(self):
        self.assertTrue(self.tenant.has_feature(FEATURE_API_INTEGRATIONS))
        self.tenant.plan = "basic"
        self.assertFalse(self.tenant.has_feature(FEATURE_API_INTEGRATIONS))

    def test_csv_preview_uses_same_mapping_pipeline(self):
        integration = make_integration(self.tenant, self.user, integration_type="csv", endpoint_url="", response_array_path="")
        csv_file = BytesIO(b"codigo,descripcion,precio,unidad\nA1,Arena,20,m3\n")
        csv_file.name = "catalog.csv"

        response = self.client.post(
            f"/api/integrations/catalog/{integration.id}/preview/",
            {"csv_file": csv_file},
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["mapped_preview"][0]["name"], "Arena")

    def test_tenant_isolation_blocks_other_tenant_integration(self):
        other_tenant = make_tenant("tenant-other")
        other_user = make_user(other_tenant)
        other_integration = make_integration(other_tenant, other_user)

        response = self.client.get(f"/api/integrations/catalog/{other_integration.id}/")

        self.assertEqual(response.status_code, 404)

    def test_sync_logs_endpoint_is_tenant_scoped(self):
        integration = make_integration(self.tenant, self.user)
        log = CatalogSyncEngine(
            integration,
            triggered_by_user=self.user,
            records=[{"codigo": "A1", "descripcion": "Arena", "precio": "20", "unidad": "m3"}],
        ).run()
        other_tenant = make_tenant("tenant-logs-other")
        other_user = make_user(other_tenant)
        other_integration = make_integration(other_tenant, other_user)
        IntegrationSyncLog.objects.create(
            integration=other_integration,
            tenant=other_tenant,
            status="success",
            started_at=log.started_at,
            triggered_by="manual",
            triggered_by_user=other_user,
        )

        response = self.client.get(f"/api/integrations/catalog/{integration.id}/logs/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(str(response.data[0]["id"]), str(log.id))

    @patch("apps.integrations.management.commands.run_catalog_integration_syncs.CatalogSyncEngine")
    def test_scheduled_sync_ignores_tenants_without_allowed_plan(self, engine_cls):
        self.tenant.plan = "basic"
        self.tenant.save(update_fields=["plan"])
        make_integration(
            self.tenant,
            self.user,
            sync_mode="scheduled",
            sync_frequency_hours=1,
            next_sync_at=timezone.now(),
            is_active=True,
        )

        premium_tenant = make_tenant("tenant-scheduled-premium", plan="premium")
        premium_user = make_user(premium_tenant)
        make_integration(
            premium_tenant,
            premium_user,
            sync_mode="scheduled",
            sync_frequency_hours=1,
            next_sync_at=timezone.now(),
            is_active=True,
        )

        call_command("run_catalog_integration_syncs")

        self.assertEqual(engine_cls.call_count, 1)
