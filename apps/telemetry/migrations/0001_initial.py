import uuid
import django.db.models.deletion
import django.db.models.functions
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("tenants", "0004_tenant_plan_free"),
        ("users", "0002_user_email_verified_emailverificationtoken_passwordresettoken"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── UserSession ───────────────────────────────────────────────────────
        migrations.CreateModel(
            name="UserSession",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True, unpack_ipv4=True)),
                ("browser", models.CharField(blank=True, max_length=60)),
                ("os", models.CharField(blank=True, max_length=60)),
                ("device_type", models.CharField(blank=True, max_length=20)),
                ("user_agent", models.CharField(blank=True, max_length=500)),
                ("device_fingerprint", models.CharField(blank=True, max_length=32)),
                ("login_at", models.DateTimeField(auto_now_add=True)),
                ("last_seen_at", models.DateTimeField(auto_now_add=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(default=dict)),
                (
                    "tenant",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sessions",
                        to="tenants.tenant",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sessions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-login_at"],
            },
        ),
        migrations.AddIndex(
            model_name="usersession",
            index=models.Index(fields=["user_id", "login_at"], name="tel_session_user_login_idx"),
        ),
        migrations.AddIndex(
            model_name="usersession",
            index=models.Index(fields=["user_id", "device_fingerprint"], name="tel_session_user_fp_idx"),
        ),
        migrations.AddIndex(
            model_name="usersession",
            index=models.Index(
                fields=["user_id", "last_seen_at"],
                condition=models.Q(revoked_at__isnull=True),
                name="tel_session_user_active_idx",
            ),
        ),
        # ── AuthEvent ─────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="AuthEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("event_type", models.CharField(
                    choices=[
                        ("login_success", "Login exitoso"),
                        ("login_failed", "Login fallido"),
                        ("logout", "Logout"),
                        ("token_refresh", "Token actualizado"),
                        ("email_verified", "Email verificado"),
                        ("email_verification_resent", "Verificacion reenviada"),
                        ("password_reset_requested", "Reset solicitado"),
                        ("password_reset_completed", "Reset completado"),
                        ("account_locked", "Cuenta bloqueada"),
                        ("new_device_login", "Login nuevo dispositivo"),
                        ("suspicious_login", "Login sospechoso"),
                        ("session_revoked", "Sesion revocada"),
                        ("register_success", "Registro exitoso"),
                        ("login_unverified_email", "Login email no verificado"),
                        ("login_blocked", "Login bloqueado"),
                    ],
                    max_length=50,
                )),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True, unpack_ipv4=True)),
                ("country_code", models.CharField(blank=True, max_length=2)),
                ("browser", models.CharField(blank=True, max_length=60)),
                ("os", models.CharField(blank=True, max_length=60)),
                ("device_type", models.CharField(blank=True, max_length=20)),
                ("user_agent", models.CharField(blank=True, max_length=500)),
                ("success", models.BooleanField(default=True)),
                ("risk_score", models.SmallIntegerField(default=0)),
                ("metadata", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "session",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="events",
                        to="telemetry.usersession",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="auth_events",
                        to="tenants.tenant",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="auth_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="authevent",
            index=models.Index(fields=["tenant_id", "created_at"], name="tel_authevent_tenant_time_idx"),
        ),
        migrations.AddIndex(
            model_name="authevent",
            index=models.Index(fields=["user_id", "created_at"], name="tel_authevent_user_time_idx"),
        ),
        migrations.AddIndex(
            model_name="authevent",
            index=models.Index(fields=["event_type", "created_at"], name="tel_authevent_type_time_idx"),
        ),
        migrations.AddIndex(
            model_name="authevent",
            index=models.Index(fields=["ip_address", "created_at"], name="tel_authevent_ip_time_idx"),
        ),
        migrations.AddIndex(
            model_name="authevent",
            index=models.Index(
                fields=["tenant_id", "created_at"],
                condition=models.Q(success=False),
                name="tel_ae_tenant_failed_idx",
            ),
        ),
    ]
