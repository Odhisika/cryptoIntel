# Crypto Intelligence Platform

10× gem scanner for crypto — ingests market data from CoinGecko, DefiLlama, DEX Screener, and Binance, scores every token on 4 dimensions (10X Potential, Undervaluation, Momentum, Risk), and exposes results via a REST API.

## Prerequisites

**With Docker (recommended):**
- Docker + Docker Compose v2

**Without Docker:**
- Python 3.11+
- PostgreSQL 16+ (or use SQLite for local dev)
- Redis 7+

---

## Quick Start — Docker

```bash
git clone <repo-url> && cd crypto-intel

# Copy environment variables (edit .env to add API keys)
cp .env.example .env

# Build and start everything
docker compose up -d

# Verify it's running
docker compose ps
curl http://localhost:8000/api/v1/assets/
```

This starts 5 containers:

| Service | What it does | Port |
|---------|-------------|------|
| `web` | Django API (Gunicorn) | **8000** |
| `celery-worker` | Runs ingestion + scoring tasks | — |
| `celery-beat` | Schedules recurring tasks | — |
| `db` | PostgreSQL 16 | 5432 |
| `redis` | Redis 7 | 6379 |

### Useful Docker commands

```bash
# Logs
docker compose logs -f web
docker compose logs -f celery-worker

# Run management commands
docker compose exec web python manage.py populate_universe
docker compose exec web python manage.py populate_protocols
docker compose exec web python manage.py populate_dex_data
docker compose exec web python manage.py populate_market_regime
docker compose exec web python manage.py show_rankings 10x_potential
docker compose exec web python manage.py run_backtest

# Restart after code changes
docker compose build web && docker compose up -d

# Stop everything
docker compose down

# Stop + wipe database
docker compose down -v
```

---

## Running Locally (no Docker)

```bash
cd backend

# Create virtualenv and install deps
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run tests (SQLite in-memory, no setup needed)
python -m pytest -q

# Local dev server (SQLite by default)
python manage.py migrate
python manage.py runserver

# Start Celery worker + beat (needs Redis running)
celery -A config worker -l info
celery -A config beat -l info
```

To use Postgres locally instead of SQLite, set these env vars:

```bash
export DB_ENGINE=django.db.backends.postgresql
export DB_NAME=crypto_intel
export DB_USER=crypto_intel
export DB_PASSWORD=your_password
export DB_HOST=localhost
export DB_PORT=5432
export REDIS_URL=redis://localhost:6379/0
```

---

## Running Tests

```bash
cd backend

# All tests (438 tests, ~30 seconds)
python -m pytest

# With verbose output
python -m pytest -v

# Run a specific test file
python -m pytest core/tests/test_dexscreener_provider.py -v

# Run a specific test class
python -m pytest core/tests/test_binance_provider.py::TestFetchCandles -v
```

---

## Environment Variables

All configurable via env vars (or a `.env` file in the project root).

| Variable | Default | Description |
|----------|---------|-------------|
| `DJANGO_SECRET_KEY` | `dev-only-not-for-production` | Django secret key |
| `DJANGO_DEBUG` | `True` | Debug mode |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated allowed hosts |
| `DB_ENGINE` | `django.db.backends.sqlite3` | Database backend |
| `DB_NAME` | `db.sqlite3` | Database name |
| `DB_USER` | *(empty)* | Database user |
| `DB_PASSWORD` | *(empty)* | Database password |
| `DB_HOST` | *(empty)* | Database host |
| `DB_PORT` | *(empty)* | Database port |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker + result backend |
| `COINGECKO_API_KEY` | *(empty)* | CoinGecko API key (optional) |
| `GITHUB_TOKEN` | *(empty)* | GitHub API token (optional) |
| `BINANCE_API_KEY` | *(empty)* | Binance API key (optional) |
| `EMAIL_BACKEND` | `django.core.mail.backends.console.EmailBackend` | Alert email backend (use `smtp.EmailBackend` for SendGrid/SES in prod) |
| `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` / `EMAIL_USE_TLS` | *(empty)* / `587` / *(empty)* | SMTP settings for email alerts |
| `DEFAULT_FROM_EMAIL` | `Crypto Intel <alerts@example.com>` | From address for alert emails |
| `TELEGRAM_BOT_TOKEN` | *(empty)* | Telegram bot token for alert delivery |

---

## Populating Data

After the server is running, seed the database:

```bash
# 1. Populate the market universe from CoinGecko (assets to track)
docker compose exec web python manage.py populate_universe \
  --min-market-cap=10000000 --max-market-cap=2000000000

# 2. Link DeFi protocols (for TVL/fee/revenue data)
docker compose exec web python manage.py populate_protocols

# 3. Backfill contract addresses (for DEX data)
docker compose exec web python manage.py populate_contract_addresses

# 4. Classify assets into sectors
docker compose exec web python manage.py populate_sectors

# 5. Backfill DEX Screener data
docker compose exec web python manage.py populate_dex_data

# 6. Seed market regime (BTC/ETH trend from Binance)
docker compose exec web python manage.py populate_market_regime
```

After populating, run the scoring engine:

```bash
docker compose exec web python manage.py score_all_assets
docker compose exec web python manage.py show_rankings 10x_potential
```

Once Celery Beat is running, all of this happens automatically on schedule:

| Task | Interval |
|------|----------|
| Price ingestion | every 15 min |
| Scoring | every 15 min |
| Market regime (Binance) | every 15 min |
| **Alerts** | **every 15 min** |
| TVL ingestion | hourly |
| Fee/revenue ingestion | hourly |
| DEX Screener ingestion | hourly |
| Holder snapshots | every 6 hours |
| Developer activity | every 6 hours |

---

## API Endpoints

```
GET /api/v1/assets/           — List all tracked assets
GET /api/v1/scores/           — List all score snapshots
GET /api/v1/snapshots/        — List market snapshots
GET /api/v1/dex-pairs/        — List DEX pair snapshots
GET /api/v1/market-regimes/   — List market regime snapshots
GET /api/v1/backtest/         — Historical score accuracy stats (win rate, avg return, Sharpe per tier/horizon)
GET /api/v1/alerts/rules/     — List/create alert rules (auth)
GET/PATCH/DELETE /api/v1/alerts/rules/<id>/  — Manage one alert rule (auth)
GET /api/v1/alerts/history/   — Alert history log (auth)
GET/POST /api/v1/webhooks/    — List/create score-change webhooks (auth)
GET/PATCH/DELETE /api/v1/webhooks/<id>/  — Manage one webhook (auth)
GET /api/v1/usage/            — Per-1,000-call API usage for the authenticated user (auth)
POST /api/v1/telegram/verify/ — Verify a Telegram chat↔account binding (auth)
POST /api/webhooks/telegram/  — Telegram bot update webhook (CSRF-exempt, optional secret token)
POST /api/webhooks/paystack/  — Paystack payment webhook (signature-verified)
GET /api/docs/                — Interactive Swagger UI (API documentation portal)
GET /api/redoc/               — ReDoc documentation portal
GET /api/schema/              — Raw OpenAPI schema (JSON)
GET /admin/                   — Django admin
```

All endpoints support `?page=1&page_size=50` pagination.

---

## Telegram Bot

The bot receives user messages via the `POST /api/webhooks/telegram/` webhook
and replies through the Bot API. Register the webhook once with:

```
python manage.py set_telegram_webhook          # uses TELEGRAM_PUBLIC_BASE_URL
python manage.py set_telegram_webhook --unset  # remove it
```

Commands:
- `/start`, `/help` — help + quick-action reply keyboard
- `/score <symbol>` — instant 4-score + tier lookup for a token
- `/top [n]` — top 10X candidates by 10x_potential score (default 5)
- `/alerts` / `/alerts add <metric> <op> <value> [symbol|all]` / `/alerts remove <id>` — manage Telegram alert rules (`/alerts` needs `/link`)
- `/link` — connect the chat to your account; the one-time verify token is confirmed via the JWT-authenticated `POST /api/v1/telegram/verify/`

Set `TELEGRAM_BOT_TOKEN` (and optionally `TELEGRAM_WEBHOOK_SECRET`) in your env.

---

## Weekly Email Digest

Every Monday 09:00 UTC, active subscribers (an ACTIVE `Subscription` with an email
address) receive a weekly digest email — top 10X candidates, the latest market
regime (BTC/ETH price, 7-day change, 50-DMA position), and the biggest score
movers (10x_potential score gainers + decliners with tier changes).

Scheduled via Celery Beat (`send-weekly-digest-monday-0900-utc` →
`core/tasks/digest.send_weekly_email_digest`). Compose/send on demand with:

```
python manage.py send_weekly_digest                 # email all active subscribers
python manage.py send_weekly_digest --email you@example.com   # just one address
python manage.py send_weekly_digest --dry-run       # print, don't email
```

Composition and data gathering live in `core/digest.py` (pure/testable); a single
recipient's delivery failure is isolated so one bad mailbox never cancels the rest.

---

## Project Structure

```
crypto-intel/
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── backend/
│   ├── config/              # Django settings, URLs, Celery config
│   ├── core/
│   │   ├── providers/       # API integrations (CoinGecko, DefiLlama, DEX Screener, Binance)
│   │   ├── scoring/         # 4 scoring engines + helpers
│   │   ├── tasks/           # Celery tasks (ingestion, scoring, alerts, digest)
│   │   ├── digest.py        # Weekly email digest composition + delivery
│   │   ├── management/      # Management commands
│   │   ├── migrations/      # Database migrations
│   │   ├── models.py        # All database models
│   │   └── tests/           # 649 tests
│   └── requirements.txt
└── docs/
    └── DATA_LICENSING.md    # API rate limits, licensing notes
```
