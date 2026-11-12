from dataclasses import dataclass

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied


PLAN_FREE = "free"
PLAN_BASIC = "basic"
PLAN_PREMIUM = "premium"
PLAN_BUSINESS = "business"

PLAN_CHOICES = (
    (PLAN_FREE, "Free"),
    (PLAN_BASIC, "Basic"),
    (PLAN_PREMIUM, "Premium"),
    (PLAN_BUSINESS, "Business"),
)

FEATURE_API_INTEGRATIONS = "api_integrations"

QUOTA_MONTHLY_QUOTES = "monthly_quotes"
QUOTA_MAX_USERS = "max_users"

UNLIMITED = None
TRIAL_EXPIRED_MESSAGE = "Tu prueba gratuita de 30 días terminó. Actualiza tu plan para continuar."


@dataclass(frozen=True)
class FeatureDefinition:
    code: str
    allowed_plans: frozenset[str]
    denial_message: str


@dataclass(frozen=True)
class PlanLimits:
    monthly_quotes: int | None  # None = unlimited
    max_users: int | None       # None = unlimited


FEATURES = {
    FEATURE_API_INTEGRATIONS: FeatureDefinition(
        code=FEATURE_API_INTEGRATIONS,
        allowed_plans=frozenset({PLAN_PREMIUM, PLAN_BUSINESS}),
        denial_message="Tu plan no incluye integraciones de catalogo. Actualiza a Premium o Business para usar esta funcion.",
    ),
}

# El registro inicia una prueba de 30 días con las capacidades del plan Básico.
PLAN_LIMITS: dict[str, PlanLimits] = {
    PLAN_FREE:     PlanLimits(monthly_quotes=50,        max_users=1),
    PLAN_BASIC:    PlanLimits(monthly_quotes=50,        max_users=1),
    PLAN_PREMIUM:  PlanLimits(monthly_quotes=UNLIMITED, max_users=3),
    PLAN_BUSINESS: PlanLimits(monthly_quotes=UNLIMITED, max_users=UNLIMITED),
}

_QUOTA_MESSAGES = {
    QUOTA_MONTHLY_QUOTES: (
        "Has alcanzado el limite de {limit} cotizaciones por mes de tu plan. "
        "Actualiza tu plan para continuar."
    ),
    QUOTA_MAX_USERS: (
        "Has alcanzado el limite de {limit} usuario(s) de tu plan. "
        "Actualiza tu plan para agregar mas usuarios."
    ),
}


def tenant_has_feature(tenant, feature_code: str) -> bool:
    feature = FEATURES[feature_code]
    return bool(tenant and tenant.plan in feature.allowed_plans)


def require_feature(tenant, feature_code: str) -> None:
    feature = FEATURES[feature_code]
    if not tenant_has_feature(tenant, feature_code):
        raise PermissionDenied(feature.denial_message)


class PlanService:
    @staticmethod
    def trial_expired(tenant) -> bool:
        return bool(tenant and tenant.plan == PLAN_FREE and timezone.now() >= tenant.trial_ends_at)

    @staticmethod
    def require_active_trial(tenant) -> None:
        if PlanService.trial_expired(tenant):
            raise PermissionDenied(TRIAL_EXPIRED_MESSAGE)

    @staticmethod
    def has_feature(tenant, feature_code: str) -> bool:
        return tenant_has_feature(tenant, feature_code)

    @staticmethod
    def require_feature(tenant, feature_code: str) -> None:
        require_feature(tenant, feature_code)

    @staticmethod
    def get_limits(tenant) -> PlanLimits:
        return PLAN_LIMITS.get(tenant.plan, PLAN_LIMITS[PLAN_FREE])

    @staticmethod
    def within_quota(tenant, quota_code: str, current_count: int) -> bool:
        """True si está dentro del límite, False si lo superó."""
        limits = PlanService.get_limits(tenant)
        limit: int | None = getattr(limits, quota_code, None)
        return limit is None or current_count < limit

    @staticmethod
    def require_quota(tenant, quota_code: str, current_count: int) -> None:
        """Lanza PermissionDenied si el tenant superó la quota del plan."""
        limits = PlanService.get_limits(tenant)
        limit: int | None = getattr(limits, quota_code, None)
        if limit is not None and current_count >= limit:
            template = _QUOTA_MESSAGES.get(quota_code, "Limite de plan alcanzado. Actualiza tu plan.")
            raise PermissionDenied(template.format(limit=limit))
