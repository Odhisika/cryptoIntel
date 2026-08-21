"""
Test settings — SQLite in-memory so tests run without a Postgres instance.
CI/dev test runs use this; real environments (including local dev against
real data) use config.settings with actual Postgres.
"""

from .settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Deterministic values so auth/webhook tests don't depend on env vars or
# the dev defaults from .env.
JWT_SIGNING_KEY = "test-jwt-signing-key-not-for-production"
JWT_ALGORITHM = "HS256"
PAYSTACK_SECRET_KEY = "sk_test_paystack-webhook-secret-for-tests"
