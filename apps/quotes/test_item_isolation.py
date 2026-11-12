from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.catalog.models import CatalogItem
from apps.clients.models import Client
from apps.quotes.models import Quote, QuoteItem
from apps.quotes.services import recalculate_quote
from apps.tenants.models import Tenant
from apps.users.models import User


class ItemIsolationTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(slug="item-a", business_name="A", email="a@example.com")
        other = Tenant.objects.create(slug="item-b", business_name="B", email="b@example.com")
        self.user = User.objects.create_user(email="a@example.com", password="Secure!Pass99", tenant=self.tenant, email_verified=True)
        self.client.force_authenticate(self.user)
        customer = Client.objects.create(tenant=self.tenant, name="Client A", email="client@example.com")
        self.quote = Quote.objects.create(tenant=self.tenant, quote_number="A-1", client=customer, created_by=self.user, title="Test", issue_date=timezone.localdate())
        self.local_item = CatalogItem.objects.create(tenant=self.tenant, name="Local", unit_price=Decimal("10"))
        self.foreign_item = CatalogItem.objects.create(tenant=other, name="Foreign", unit_price=Decimal("10"))
        self.item = QuoteItem.objects.create(quote=self.quote, catalog_item=self.local_item, description="Local", unit="unidad", quantity=1, unit_price=10)
        recalculate_quote(self.quote)

    def test_patch_rejects_foreign_catalog_item(self):
        url = f"/api/quotes/{self.quote.pk}/items/{self.item.pk}/"
        response = self.client.patch(url, {"catalog_item": str(self.foreign_item.pk)}, format="json")
        self.assertEqual(response.status_code, 400)
        self.item.refresh_from_db()
        self.assertEqual(self.item.catalog_item_id, self.local_item.pk)

    def test_patch_rejects_inactive_catalog_item(self):
        self.local_item.is_active = False
        self.local_item.save(update_fields=["is_active"])
        url = f"/api/quotes/{self.quote.pk}/items/{self.item.pk}/"
        response = self.client.patch(url, {"catalog_item": str(self.local_item.pk)}, format="json")
        self.assertEqual(response.status_code, 400)
