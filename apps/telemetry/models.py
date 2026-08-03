import uuid

from django.conf import settings
from django.db import models


class UserSession(models.Model):
    """
    Tracks authenticated sessions.  Updated (last_seen_at) on token refresh.
    Never deleted — set revoked_at to invalidate.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sessions",
        db_index=False,  # covered by composite tel_session_user_login_idx
    )
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="sessions",
        db_index=False,
    )

    ip_address = models.GenericIPAddressField(null=True, blank=True, unpack_ipv4=True)
    browser = models.CharField(max_length=60, blank=True)
    os = models.CharField(max_length=60, blank=True)
    device_type = models.CharField(max_length=20, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    # SHA-256[:32] of "browser|os|user_agent[:200]" — new-device detection
    device_fingerprint = models.CharField(max_length=32, blank=True, db_index=False)

    login_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    # Flexible bag: geo enrichment placeholder, extra client hints, etc.
    metadata = models.JSONField(default=dict)

    class Meta:
        app_label = "telemetry"
        ordering = ["-login_at"]
        indexes = [
            # Active session list for a user (most common API call)
            models.Index(
                fields=["user_id", "login_at"],
                name="tel_session_user_login_idx",
            ),
            # New-device detection: does this fingerprint exist for the user?
            models.Index(
                fields=["user_id", "device_fingerprint"],
                name="tel_session_user_fp_idx",
            ),
            # Partial: active sessions only (non-revoked)
            models.Index(
                fields=["user_id", "last_seen_at"],
                condition=models.Q(revoked_at__isnull=True),
                name="tel_session_user_active_idx",
            ),
        ]

    def __str__(self):
        return f"Session({self.user_id} / {self.browser} / {self.login_at:%Y-%m-%d})"


class AuthEvent(models.Model):
    """
    Append-only auth event log.  Never updated or soft-deleted.

    Index strategy
    ──────────────
    • Composite (tenant_id, created_at) — tenant dashboard timeline
    • Composite (user_id,   created_at) — user profile login history
    • Composite (event_type, created_at) — analytics aggregations
    • Composite (ip_address, created_at) — IP-based security queries
    • Partial   (tenant_id, created_at) WHERE success=False — failed-login monitoring
    • NO standalone FK indexes (covered by composites, db_index=False on FKs)
    • NO GIN on metadata — write-heavy append-only table; add GIN only when needed
    """

    class EventType(models.TextChoices):
        LOGIN_SUCCESS = "login_success", "Login exitoso"
        LOGIN_FAILED = "login_failed", "Login fallido"
        LOGOUT = "logout", "Logout"
        TOKEN_REFRESH = "token_refresh", "Token actualizado"
        EMAIL_VERIFIED = "email_verified", "Email verificado"
        EMAIL_VERIFICATION_RESENT = "email_verification_resent", "Verificacion reenviada"
        PASSWORD_RESET_REQUESTED = "password_reset_requested", "Reset solicitado"
        PASSWORD_RESET_COMPLETED = "password_reset_completed", "Reset completado"
        ACCOUNT_LOCKED = "account_locked", "Cuenta bloqueada"
        NEW_DEVICE_LOGIN = "new_device_login", "Login nuevo dispositivo"
        SUSPICIOUS_LOGIN = "suspicious_login", "Login sospechoso"
        SESSION_REVOKED = "session_revoked", "Sesion revocada"
        REGISTER_SUCCESS = "register_success", "Registro exitoso"
        LOGIN_UNVERIFIED_EMAIL = "login_unverified_email", "Login email no verificado"
        LOGIN_BLOCKED = "login_blocked", "Login bloqueado"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auth_events",
        db_index=False,
    )
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auth_events",
        db_index=False,
    )
    session = models.ForeignKey(
        UserSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        db_index=False,
    )

    event_type = models.CharField(max_length=50, choices=EventType.choices)
    ip_address = models.GenericIPAddressField(null=True, blank=True, unpack_ipv4=True)
    # country_code: filled asynchronously by GeoIP enrichment task (future)
    country_code = models.CharField(max_length=2, blank=True)
    browser = models.CharField(max_length=60, blank=True)
    os = models.CharField(max_length=60, blank=True)
    device_type = models.CharField(max_length=20, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    success = models.BooleanField(default=True)
    risk_score = models.SmallIntegerField(default=0)  # 0–100

    # JSONB bag:
    # {
    #   "browser_version": "136.0",
    #   "is_mobile": false,
    #   "geo": {"country_code": null, "city": null},   ← async-filled
    #   "risk_factors": ["new_device"],
    #   "email": "user@x.com",                         ← for failed logins without user FK
    #   "error": "Invalid credentials"
    # }
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=False)

    class Meta:
        app_label = "telemetry"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["tenant_id", "created_at"],
                name="tel_authevent_tenant_time_idx",
            ),
            models.Index(
                fields=["user_id", "created_at"],
                name="tel_authevent_user_time_idx",
            ),
            models.Index(
                fields=["event_type", "created_at"],
                name="tel_authevent_type_time_idx",
            ),
            models.Index(
                fields=["ip_address", "created_at"],
                name="tel_authevent_ip_time_idx",
            ),
            # Partial index: failed-event monitoring is a common security query
            models.Index(
                fields=["tenant_id", "created_at"],
                condition=models.Q(success=False),
                name="tel_ae_tenant_failed_idx",
            ),
        ]

    def __str__(self):
        return f"AuthEvent({self.event_type} / {self.created_at:%Y-%m-%d %H:%M})"
