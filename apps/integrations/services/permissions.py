from apps.tenants.services.plans import FEATURE_API_INTEGRATIONS, PlanService


def tenant_can_use_integrations(tenant):
    return PlanService.has_feature(tenant, FEATURE_API_INTEGRATIONS)


def ensure_tenant_can_use_integrations(tenant):
    PlanService.require_feature(tenant, FEATURE_API_INTEGRATIONS)
