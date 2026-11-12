from datetime import timedelta
from pathlib import Path
import sys
from urllib.parse import urlparse

from decouple import config


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config("DJANGO_SECRET_KEY", default="change-me-only-for-local-development")
DEBUG = config("DJANGO_DEBUG", default=True, cast=bool)
ALLOWED_HOSTS = config("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1,testserver").split(",")
if not DEBUG and SECRET_KEY == "change-me-only-for-local-development":
    raise ValueError("DJANGO_SECRET_KEY must be configured for production.")

DEFAULT_FRONTEND_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000"
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in config("CORS_ALLOWED_ORIGINS", default=DEFAULT_FRONTEND_ORIGINS).split(",")
    if origin.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in config("CSRF_TRUSTED_ORIGINS", default=DEFAULT_FRONTEND_ORIGINS).split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "drf_spectacular",
    "apps.common",
    "apps.tenants",
    "apps.users",
    "apps.telemetry",
    "apps.clients",
    "apps.catalog",
    "apps.integrations",
    "apps.quotes",
    "apps.documents",
    "apps.notifications",
]

MIDDLEWARE = [
    "apps.telemetry.middleware.RequestIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASE_URL = config("DATABASE_URL", default="")
if DATABASE_URL and DATABASE_URL.startswith(("postgres://", "postgresql://")):
    parsed_db = urlparse(DATABASE_URL)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed_db.path.lstrip("/"),
            "USER": parsed_db.username or "",
            "PASSWORD": parsed_db.password or "",
            "HOST": parsed_db.hostname or "",
            "PORT": str(parsed_db.port or 5432),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": config("DB_NAME", default="cotix_dev"),
            "USER": config("DB_USER", default="postgres"),
            "PASSWORD": config("DB_PASSWORD", default=""),
            "HOST": config("DB_HOST", default="localhost"),
            "PORT": config("DB_PORT", default="5432"),
        }
    }

DB_SCHEMA = config("DB_SCHEMA", default="")
if "test" in sys.argv:
    DB_SCHEMA = config("TEST_DB_SCHEMA", default="")
if DB_SCHEMA:
    DATABASES["default"]["OPTIONS"] = {
        "options": f"-c search_path={DB_SCHEMA}",
    }

AUTH_USER_MODEL = "users.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es-gt"
TIME_ZONE = "America/Guatemala"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "apps.users.authentication.SessionJWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.ScopedRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "login": "5/min",
        "register": "3/hour",
        "verify_email": "10/hour",
        "verify_email_resend": "3/hour",
        "password_reset": "5/hour",
        "send_email": "10/hour",
        "public_quote_respond": "10/hour",
        "public_quote_view": "60/hour",
    },
    "EXCEPTION_HANDLER": "apps.common.exceptions.safe_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=config("JWT_ACCESS_LIFETIME_MINUTES", default=15, cast=int)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=config("JWT_REFRESH_LIFETIME_DAYS", default=30, cast=int)),
    "CHECK_REVOKE_TOKEN": True,
}

REFRESH_TOKEN_COOKIE_NAME = config("REFRESH_TOKEN_COOKIE_NAME", default="cotix_refresh")
REFRESH_TOKEN_COOKIE_PATH = config("REFRESH_TOKEN_COOKIE_PATH", default="/api/auth/")
REFRESH_TOKEN_COOKIE_SECURE = config("REFRESH_TOKEN_COOKIE_SECURE", default=False, cast=bool)
REFRESH_TOKEN_COOKIE_SAMESITE = config("REFRESH_TOKEN_COOKIE_SAMESITE", default="Lax")
if REFRESH_TOKEN_COOKIE_SAMESITE.lower() == "none" and not REFRESH_TOKEN_COOKIE_SECURE:
    raise ValueError("REFRESH_TOKEN_COOKIE_SAMESITE=None requires REFRESH_TOKEN_COOKIE_SECURE=True.")

SPECTACULAR_SETTINGS = {
    "TITLE": "Cotix API",
    "DESCRIPTION": "Backend MVP for Cotix quotation SaaS.",
    "VERSION": "0.1.0",
}

CORS_ALLOW_ALL_ORIGINS = config("CORS_ALLOW_ALL_ORIGINS", default=False, cast=bool)
CORS_ALLOW_CREDENTIALS = config("CORS_ALLOW_CREDENTIALS", default=True, cast=bool)
if not DEBUG and CORS_ALLOW_ALL_ORIGINS:
    raise ValueError("CORS_ALLOW_ALL_ORIGINS must be disabled for production.")
if CORS_ALLOW_ALL_ORIGINS and CORS_ALLOW_CREDENTIALS:
    raise ValueError("CORS_ALLOW_ALL_ORIGINS cannot be combined with credentialed requests.")

if DEBUG:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
else:
    EMAIL_BACKEND = config(
        "EMAIL_BACKEND",
        default="django.core.mail.backends.smtp.EmailBackend",
    )
    EMAIL_HOST = config("EMAIL_HOST", default="smtp.resend.com")
    EMAIL_PORT = config("EMAIL_PORT", default=465, cast=int)
    EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="resend")
    EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
    EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=False, cast=bool)
    EMAIL_USE_SSL = config("EMAIL_USE_SSL", default=True, cast=bool)
    if not EMAIL_HOST_PASSWORD:
        raise ValueError("EMAIL_HOST_PASSWORD (Resend API key) must be set in production.")
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="Cotix <no-reply@cotix.local>")

FRONTEND_URL = config("FRONTEND_URL", default="http://localhost:3000")

SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=False, cast=bool)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = config("SESSION_COOKIE_SECURE", default=False, cast=bool)
CSRF_COOKIE_SECURE = config("CSRF_COOKIE_SECURE", default=False, cast=bool)
SESSION_COOKIE_HTTPONLY = config("SESSION_COOKIE_HTTPONLY", default=True, cast=bool)
CSRF_COOKIE_HTTPONLY = config("CSRF_COOKIE_HTTPONLY", default=False, cast=bool)
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=0, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = config("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False, cast=bool)
SECURE_HSTS_PRELOAD = config("SECURE_HSTS_PRELOAD", default=False, cast=bool)
SECURE_CONTENT_TYPE_NOSNIFF = config("SECURE_CONTENT_TYPE_NOSNIFF", default=True, cast=bool)
X_FRAME_OPTIONS = config("X_FRAME_OPTIONS", default="DENY")
USE_X_FORWARDED_HOST = config("USE_X_FORWARDED_HOST", default=False, cast=bool)

# --- Stripe ---
STRIPE_SECRET_KEY = config("STRIPE_SECRET_KEY", default="")
STRIPE_WEBHOOK_SECRET = config("STRIPE_WEBHOOK_SECRET", default="")
# Map (plan, interval) → Stripe Price ID. Set these in .env.
STRIPE_PRICES: dict[tuple[str, str], str] = {
    ("basic",    "monthly"): config("STRIPE_PRICE_BASIC_MONTHLY",    default=""),
    ("premium",  "monthly"): config("STRIPE_PRICE_PREMIUM_MONTHLY",  default=""),
    ("premium",  "annual"):  config("STRIPE_PRICE_PREMIUM_ANNUAL",   default=""),
    ("business", "monthly"): config("STRIPE_PRICE_BUSINESS_MONTHLY", default=""),
}

EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS = config("EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS", default=24, cast=int)
PASSWORD_RESET_TOKEN_EXPIRE_MINUTES = config("PASSWORD_RESET_TOKEN_EXPIRE_MINUTES", default=30, cast=int)

LOGIN_FAILED_LIMIT = config("LOGIN_FAILED_LIMIT", default=5, cast=int)
LOGIN_FAILED_WINDOW_MINUTES = config("LOGIN_FAILED_WINDOW_MINUTES", default=10, cast=int)
LOGIN_LOCKOUT_MINUTES = config("LOGIN_LOCKOUT_MINUTES", default=15, cast=int)
INTEGRATION_ENCRYPTION_KEY = config("INTEGRATION_ENCRYPTION_KEY", default=SECRET_KEY)
INTEGRATION_REQUEST_TIMEOUT_SECONDS = config("INTEGRATION_REQUEST_TIMEOUT_SECONDS", default=15, cast=int)
INTEGRATION_CSV_MAX_BYTES = config("INTEGRATION_CSV_MAX_BYTES", default=2 * 1024 * 1024, cast=int)
INTEGRATION_CSV_MAX_ROWS = config("INTEGRATION_CSV_MAX_ROWS", default=5000, cast=int)
INTEGRATION_CSV_MAX_FIELDS = config("INTEGRATION_CSV_MAX_FIELDS", default=50, cast=int)
INTEGRATION_CSV_MAX_CELL_LENGTH = config("INTEGRATION_CSV_MAX_CELL_LENGTH", default=1000, cast=int)

LOG_LEVEL = config("LOG_LEVEL", default="INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "redact_sensitive": {
            "()": "apps.common.logging.RedactSensitiveFilter",
        }
    },
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "filters": ["redact_sensitive"],
        }
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": True},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "cotix.security": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "apps.users": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}
