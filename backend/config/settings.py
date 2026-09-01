"""
Django settings — dev defaults. Nothing here is production-ready; Phase 13
(production hardening) revisits SECRET_KEY handling, ALLOWED_HOSTS, DEBUG,
TLS, and secrets management before public launch.
"""

from pathlib import Path
from celery.schedules import crontab
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config("DJANGO_SECRET_KEY", default="dev-only-not-for-production")
DEBUG = config("DJANGO_DEBUG", default=True, cast=bool)
ALLOWED_HOSTS = config("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_celery_beat",
    "drf_spectacular",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": config("DB_ENGINE", default="django.db.backends.sqlite3"),
        "NAME": config("DB_NAME", default=str(BASE_DIR / "db.sqlite3")),
        "USER": config("DB_USER", default=""),
        "PASSWORD": config("DB_PASSWORD", default=""),
        "HOST": config("DB_HOST", default=""),
        "PORT": config("DB_PORT", default=""),
    }
}
# Dev default is SQLite (zero setup). For production, set:
#   DB_ENGINE=django.db.backends.postgresql
#   DB_NAME=..., DB_USER=..., DB_PASSWORD=..., DB_HOST=..., DB_PORT=5432
# SQLite is fine through Phase 12; revisit before Phase 13 (production
# hardening) if this hasn't already moved to Postgres by then — SQLite
# does not handle concurrent writes well under real load.

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CELERY_BROKER_URL = config("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = config("REDIS_URL", default="redis://localhost:6379/0")
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]

# Ingestion cadence is deliberately conservative on the free CoinGecko tier
# to stay well under rate limits. Revisit once a paid tier / more providers
# are added — see docs/DATA_LICENSING.md.
CELERY_BEAT_SCHEDULE = {
    "ingest-market-snapshots-every-15-min": {
        "task": "core.tasks.ingestion.ingest_market_snapshots",
        "schedule": 15 * 60,
    },
    "score-all-assets-every-15-min": {
        # Runs on the same cadence as ingestion but Celery Beat doesn't
        # guarantee ordering between two schedule entries — if this
        # matters in practice (scoring against a snapshot ingestion hasn't
        # finished writing yet), chain them explicitly via a Celery chain
        # instead of two independent beat entries. Flagged as a possible
        # follow-up rather than solved now, since it depends on observed
        # timing once this runs for real.
        "task": "core.tasks.scoring.score_all_assets",
        "schedule": 15 * 60,
    },
    "ingest-tvl-snapshots-hourly": {
        # TVL moves much slower than price/volume; hourly is plenty and
        # keeps well within DefiLlama's free-tier usage norms.
        "task": "core.tasks.tvl_ingestion.ingest_tvl_snapshots",
        "schedule": 60 * 60,
    },
    "ingest-fee-revenue-snapshots-hourly": {
        "task": "core.tasks.fee_ingestion.ingest_fee_revenue_snapshots",
        "schedule": 60 * 60,
    },
    "ingest-holder-snapshots-every-6-hours": {
        # Holder data is Beta/coverage-limited per CoinGecko's own docs and
        # changes slowly; every 6h keeps well within reasonable use.
        "task": "core.tasks.holder_ingestion.ingest_holder_snapshots",
        "schedule": 6 * 60 * 60,
    },
    "ingest-developer-activity-every-6-hours": {
        # Commit activity changes slowly enough that 6h is plenty, and
        # this keeps well within GitHub's unauthenticated 60/hr limit
        # even without a GITHUB_TOKEN configured.
        "task": "core.tasks.developer_activity_ingestion.ingest_developer_activity",
        "schedule": 6 * 60 * 60,
    },
    "ingest-dex-screener-hourly": {
        # DEX Screener data changes faster than TVL but slower than price;
        # hourly keeps within their documented 300 req/min limit while
        # capturing meaningful DEX activity changes.
        "task": "core.tasks.dex_ingestion.ingest_dex_screener_data",
        "schedule": 60 * 60,
    },
    "ingest-market-regime-every-15-min": {
        # Same cadence as price ingestion — regime indicators (BTC/ETH
        # trend, dominance) should be as fresh as the price data they
        # contextualize.
        "task": "core.tasks.regime_ingestion.ingest_market_regime",
        "schedule": 15 * 60,
    },
    # Real-Time Alerts (Feature 2): evaluate rules and dispatch email/Telegram
    # deliveries. Runs on the same cadence as scoring so a fresh score crossing
    # a user's threshold is picked up within one cycle. Rule evaluation is cheap
    # (a handful of indexed lookups per rule); raise the interval if the rule
    # table grows large enough to make a full pass expensive.
    "process-alerts-every-15-min": {
        "task": "core.tasks.alerts.process_alerts",
        "schedule": 15 * 60,
    },
    # Weekly Email Digest (Feature 9): send every Monday 09:00 UTC to active
    # subscribers. To change the cadence, edit the crontab entry below.
    "send-weekly-digest-monday-0900-utc": {
        "task": "core.tasks.digest.send_weekly_email_digest",
        "schedule": crontab(minute=0, hour=9, day_of_week=1),
    },
}

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "core.auth.ExternalSiteJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "core.permissions.SubscriptionRequired",
    ],
    # OpenAPI schema generation (Feature 7 — API docs portal)
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

# API documentation portal (Feature 7) — Swagger UI + Redoc served from the
# /api/schema/ and /api/docs/ and /api/redoc/ URLs.
SPECTACULAR_SETTINGS = {
    "TITLE": "Crypto Intel API",
    "DESCRIPTION": (
        "10x gem scanner — market data ingestion, multi-dimensional scoring, "
        "and risk/reward tiers for crypto assets. Includes real-time alerts "
        "and historical score-accuracy backtesting."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    # Every data endpoint requires JWT authentication (the default permission
    # is SubscriptionRequired), so advertise the Bearer requirement globally.
    "SECURITY": [{"externalSiteJWT": []}],
}

# JWT — must match the main site's signing key and algorithm
JWT_SIGNING_KEY = config("JWT_SIGNING_KEY", default="change-me-in-production")
JWT_ALGORITHM = config("JWT_ALGORITHM", default="HS256")
JWT_USER_ID_FIELD = config("JWT_USER_ID_FIELD", default="user_id")

# Paystack — webhook secret from Paystack dashboard
PAYSTACK_SECRET_KEY = config("PAYSTACK_SECRET_KEY", default="")

COINGECKO_API_KEY = config("COINGECKO_API_KEY", default="")
GITHUB_TOKEN = config("GITHUB_TOKEN", default="")
BINANCE_API_KEY = config("BINANCE_API_KEY", default="")

# --- Real-Time Alerts (Feature 2) ---
# Email: dev default is the console backend (prints to stdout, no external
# send). Point EMAIL_BACKEND at SMTP (e.g. SendGrid/SES) in production and
# set EMAIL_HOST_USER / EMAIL_HOST_PASSWORD / DEFAULT_FROM_EMAIL.
EMAIL_BACKEND = config("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = config("EMAIL_HOST", default="")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="Crypto Intel <alerts@example.com>")
EMAIL_SUBJECT_PREFIX = config("EMAIL_SUBJECT_PREFIX", default="[Crypto Intel] ")

# Telegram: token for the bot that delivers /score, /alerts and alert
# messages. See Feature 2 / Roadmap Tier 3 Feature 8 for the bot itself.
TELEGRAM_BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default="")
# Optional secret Telegram sends in X-Telegram-Bot-Api-Secret-Token on every
# webhook call; when set, /webhooks/telegram/ rejects calls without it.
TELEGRAM_WEBHOOK_SECRET = config("TELEGRAM_WEBHOOK_SECRET", default="")
# Public base URL (scheme + host) of this deployment, used to register the bot
# webhook: <base>/api/webhooks/telegram/ via set_telegram_webhook.
TELEGRAM_PUBLIC_BASE_URL = config("TELEGRAM_PUBLIC_BASE_URL", default="https://example.com")
