from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.integrations.models import TenantCatalogIntegration
from apps.integrations.services.sync import CatalogSyncEngine
from apps.tenants.services.plans import FEATURES, FEATURE_API_INTEGRATIONS


class Command(BaseCommand):
    help = "Ejecuta las sincronizaciones programadas de catalogo vencidas."

    def handle(self, *args, **options):
        allowed_plans = FEATURES[FEATURE_API_INTEGRATIONS].allowed_plans
        due_integrations = TenantCatalogIntegration.objects.select_related("tenant").filter(
            deleted_at__isnull=True,
            is_active=True,
            sync_mode="scheduled",
            next_sync_at__lte=timezone.now(),
            tenant__is_active=True,
            tenant__status__in=["demo", "active"],
            tenant__plan__in=allowed_plans,
        )
        count = 0
        for integration in due_integrations:
            CatalogSyncEngine(integration, triggered_by="scheduled").run()
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Sincronizaciones ejecutadas: {count}"))
