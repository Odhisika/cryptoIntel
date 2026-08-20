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
