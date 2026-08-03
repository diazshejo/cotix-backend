from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import CatalogItem

from .exceptions import IntegrationError
from .fetchers import RestAPIFetcher
from .mappers import apply_filters, map_record
from .permissions import ensure_tenant_can_use_integrations
from .redaction import sanitize_error_message


BATCH_SIZE = 500


class CatalogSyncEngine:
    def __init__(self, integration, *, triggered_by="manual", triggered_by_user=None, records=None):
        self.integration = integration
        self.tenant = integration.tenant
        self.triggered_by = triggered_by
        self.triggered_by_user = triggered_by_user
        self.records = records
        self.seen_external_ids = set()
        self.now = timezone.now()

    def run(self):
        from apps.integrations.models import IntegrationSyncLog

        log = IntegrationSyncLog.objects.create(
            integration=self.integration,
            tenant=self.tenant,
            status=IntegrationSyncLog.Status.RUNNING,
            started_at=timezone.now(),
            triggered_by=self.triggered_by,
            triggered_by_user=self.triggered_by_user,
        )
        self.log = log
        self.integration.last_sync_status = "running"
        self.integration.save(update_fields=["last_sync_status", "updated_at"])
        try:
            if not self.integration.is_active:
                raise IntegrationError("La integracion esta inactiva.")
            ensure_tenant_can_use_integrations(self.tenant)
            raw_records = self._fetch_records()
            filtered_records = apply_filters(raw_records, self.integration.pre_import_filters)
            self.log.total_fetched = len(filtered_records)
            self._upsert_records(filtered_records)
            self._deactivate_absent_products()
            status = "partial" if self.log.error_count else "success"
            self._finalize(status)
        except IntegrationError as exc:
            error = sanitize_error_message(exc)
            self._add_error("", error)
            self._finalize("failed", error)
        except Exception as exc:
            self._add_error("", "Error inesperado durante la sincronizacion.")
            self._finalize("failed", sanitize_error_message(exc))
        return self.log

    def _fetch_records(self):
        if self.records is not None:
            return self.records
        if self.integration.integration_type == "rest_api":
            return RestAPIFetcher(self.integration).fetch_all()
        return []

    @transaction.atomic
    def _upsert_records(self, records):
        mapped_records = []
        for raw in records:
            try:
                mapped = map_record(raw, self.integration.field_mapping)
                self.seen_external_ids.add(mapped["external_id"])
                mapped_records.append(mapped)
            except Exception as exc:
                self._add_error(str(raw.get("id", raw.get("sku", ""))) if isinstance(raw, dict) else "", sanitize_error_message(exc))

        existing = {
            item.external_id: item
            for item in CatalogItem.objects.select_for_update().filter(
                tenant=self.tenant,
                integration=self.integration,
                external_id__in=[row["external_id"] for row in mapped_records],
                deleted_at__isnull=True,
            )
        }
        creates = []
        updates = []
        for mapped in mapped_records:
            item = existing.get(mapped["external_id"])
            if item is None:
                creates.append(
                    CatalogItem(
                        tenant=self.tenant,
                        integration=self.integration,
                        external_id=mapped["external_id"],
                        is_synced=True,
                        last_synced_at=self.now,
                        item_type=CatalogItem.ItemType.PRODUCT,
                        sku=mapped["sku"],
                        name=mapped["name"],
                        description=mapped["description"],
                        unit_price=mapped["unit_price"],
                        unit=mapped["unit"],
                        currency=self.tenant.default_currency,
                        is_active=True,
                    )
                )
                continue
            changes = self._apply_changes(item, mapped)
            if changes:
                updates.append(item)
            else:
                self.log.skipped_count += 1
        if creates:
            CatalogItem.objects.bulk_create(creates, batch_size=BATCH_SIZE)
            self.log.created_count += len(creates)
        if updates:
            CatalogItem.objects.bulk_update(
                updates,
                ["sku", "name", "description", "unit_price", "unit", "is_active", "is_synced", "last_synced_at", "updated_at"],
                batch_size=BATCH_SIZE,
            )
            self.log.updated_count += len(updates)

    def _apply_changes(self, item, mapped):
        changed = False
        if item.unit_price != mapped["unit_price"]:
            if self.integration.price_conflict_policy == "skip":
                return False
            item.unit_price = mapped["unit_price"]
            changed = True
        for field in ("sku", "name", "description", "unit"):
            value = mapped[field]
            if getattr(item, field) != value:
                setattr(item, field, value)
                changed = True
        if not item.is_active:
            item.is_active = True
            changed = True
        item.is_synced = True
        item.last_synced_at = self.now
        item.updated_at = self.now
        return changed

    def _deactivate_absent_products(self):
        queryset = CatalogItem.objects.filter(
            tenant=self.tenant,
            integration=self.integration,
            is_synced=True,
            is_active=True,
            deleted_at__isnull=True,
        ).exclude(external_id__in=self.seen_external_ids)
        count = queryset.update(is_active=False, updated_at=self.now)
        self.log.updated_count += count

    def _add_error(self, external_id, error):
        details = self.log.error_details or []
        if len(details) < 100:
            details.append({"external_id": external_id, "error": error})
        self.log.error_details = details
        self.log.error_count += 1

    def _finalize(self, status, error_detail=""):
        finished = timezone.now()
        self.log.status = status
        self.log.finished_at = finished
        self.log.duration_seconds = Decimal(str(round((finished - self.log.started_at).total_seconds(), 2)))
        self.log.save()
        next_sync_at = None
        if self.integration.sync_mode == "scheduled" and self.integration.sync_frequency_hours:
            next_sync_at = finished + timezone.timedelta(hours=self.integration.sync_frequency_hours)
        self.integration.last_synced_at = finished if status in {"success", "partial"} else self.integration.last_synced_at
        self.integration.last_sync_status = status
        self.integration.last_sync_created = self.log.created_count
        self.integration.last_sync_updated = self.log.updated_count
        self.integration.last_sync_skipped = self.log.skipped_count
        self.integration.last_sync_errors = self.log.error_count
        self.integration.last_sync_error_detail = error_detail[:1000] if error_detail else ""
        self.integration.next_sync_at = next_sync_at
        self.integration.save(
            update_fields=[
                "last_synced_at",
                "last_sync_status",
                "last_sync_created",
                "last_sync_updated",
                "last_sync_skipped",
                "last_sync_errors",
                "last_sync_error_detail",
                "next_sync_at",
                "updated_at",
            ]
        )
