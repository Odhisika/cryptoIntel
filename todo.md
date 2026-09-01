# Crypto Intelligence Platform — Feature Roadmap

Status legend: `[x]` done · `[~]` partial · `[ ]` not started

**Current state:** Data ingestion + scoring engine + REST API are complete. Auth is JWT (external site) + **Paystack** (not Stripe). Frontend is Django templates + HTMX (not React). Features 2 (Real-Time Alerts) and 3 (Backtest Accuracy) are implemented. Features 5, 6, 8, 9 remain unimplemented.

---

## Tier 1 — Makes it sellable

### 1. Authentication + API Key System
- [~] User registration/login (JWT) — **backed by external site; no in-repo registration/login**
- [~] Payments — **Paystack integrated** (`core/payments.py`, webhook `/api/webhooks/paystack/`); roadmap said Stripe — decide: keep Paystack or switch to Stripe
- [ ] Tiered access: Free / Pro / Enterprise — only a flat `Subscription.plan` field exists
- [ ] Rate limiting per API key — no API key model or rate limiting
- [ ] API key management dashboard
- [x] Subscription gating — `SubscriptionRequired` permission is the default on all endpoints
- [x] Payment webhook — Paystack signature verification + `Subscription` creation/update

### 2. Real-Time Alerts ✅ implemented
- [x] Email alerts via SendGrid/SES when token crosses score thresholds — `send_email_alert` in `core/notifications.py`; dev default is the Django console backend, swap to SMTP/SendGrid/SES via env vars
- [x] Telegram bot integration (webhook-based) — `send_telegram_alert` POSTs to the Bot API (`TELEGRAM_BOT_TOKEN`)
- [x] User-configurable alert rules — `AlertRule` model (`core/models.py`): score thresholds (10X/undervaluation/momentum/risk), market cap & volume 24h % change, per-asset or global, email/Telegram channels, cooldowns
- [x] Alert history log — `AlertEvent` model stores every fired alert + delivery status
- [x] Celery evaluation — `core/tasks/alerts.py` `process_alerts` runs on the 15-min beat schedule; pure logic in `core/alerts.py`
- [x] API — `GET/POST /api/v1/alerts/rules/`, `GET/PATCH/DELETE /api/v1/alerts/rules/<id>/`, `GET /api/v1/alerts/history/` (auth gated)
- [x] Admin — `AlertRule` and `AlertEvent` registered in Django admin
- [x] Tests — `core/tests/test_alerts.py`

### 3. Historical Score Accuracy Dashboard ✅ implemented
- [x] Data supports backtesting — append-only `MarketSnapshot` / `ScoreSnapshot` models (Phase 10-ready)
- [x] Backtest scores against actual 30/60/90-day price performance — `core/backtest.py` compares each 10X score's baseline price to the forward 30/60/90-day price
- [x] Win rate, average return, Sharpe ratio per score tier — `performance_metrics` (annualized Sharpe), bucketed by reward tier
- [x] Public-facing accuracy stats (sales pitch) — `GET /api/v1/backtest/` returns headline + per-tier/per-horizon metrics; `run_backtest` management command prints a table
- [x] No look-ahead — only scores old enough to have a full forward window are counted; one observation per asset per horizon
- [x] Tests — `core/tests/test_backtest.py`

---

## Tier 2 — Makes it sticky

### 4. Frontend Dashboard (currently Django templates + HTMX, not React/Next.js)
- [x] Score distribution overview — `dashboard.html`
- [x] Rankings page with sorting/filtering — `rankings.html`
- [x] Individual token deep-dive with price charts — `asset_detail.html`
- [x] Market regime indicator — in dashboard/regime views
- [ ] Responsive mobile design
- [ ] *(Optional)* Migrate to React/Next.js — decide if worth the rewrite

### 5. Portfolio Tracker
- [ ] Import holdings via wallet address or manual entry
- [ ] Aggregated portfolio score
- [ ] P&L tracking against score predictions
- [ ] Alerts when portfolio average drops below threshold

### 6. Custom Screener Filters
- [~] Basic filter — query-param filtering on asset list (search, sector, tier, is_active, sort)
- [ ] Filter by sector, score ranges, market cap, DEX liquidity
- [ ] Save custom screens
- [ ] Share screens with other users
- [ ] Pro feature: advanced filters (token age, whale concentration, etc.)

---

## Tier 3 — Growth loop

### 7. Public Score API (REST only so far)
- [x] REST endpoints — `/api/v1/assets/`, `/search/`, `/tiers/`, `/scores/ranking/`, `/protocols/`, `/catalysts/`, `/dashboard/stats/`
- [ ] GraphQL endpoints — not present (decide if needed)
- [x] B2B pricing (per 1,000 calls) — `ApiUsage` per-user daily count, auto-incremented on every authenticated request; surfaced via `GET /api/v1/usage/`
- [x] API documentation portal — drf-spectacular Swagger (`/api/docs/`) + Redoc (`/api/redoc/`) + OpenAPI schema (`/api/schema/`), JWT Bearer security advertised
- [x] Webhook delivery for score changes — `WebhookSubscription` model + `GET/POST /api/v1/webhooks/`, `GET/PATCH/DELETE /api/v1/webhooks/<id>/`; `core/webhooks.py` fires HMAC-signed `score.changed` POSTs when an asset's reward tier changes after a scoring run
- [x] Tests — `core/tests/test_webhooks.py`

### 8. Telegram Bot
- [x] `/score <symbol>` — instant score lookup — `core/tgbot.py` + webhook `POST /api/webhooks/telegram/`
- [x] `/alerts` — manage alert subscriptions — list/add/remove Telegram alert rules (requires /link binding)
- [x] `/top [n]` — daily top 10X candidates
- [x] `/link` — secure chat→account binding via JWT-verified `POST /api/v1/telegram/verify/`
- [x] Quick-action reply keyboards (main menu buttons for /top, /score, /alerts, /link)
- [x] `set_telegram_webhook` management command to register/unregister the bot webhook
- [x] Tests — `core/tests/test_tgbot.py`

### 9. Weekly Email Digest
- [x] "Top 10 10X candidates this week" — `core/digest.py` `top_candidates()` (top by latest 10x score + tier)
- [x] Market regime summary — `core/digest.py` `latest_regime()` (latest `MarketRegimeSnapshot`: regime, confidence, BTC/ETH price + 7d, 50DMA)
- [x] Biggest score movers (upgrades + downgrades) — `core/digest.py` `score_movers()` (latest vs previous 10x snapshot delta, gainers/decliners, tier changes)
- [x] Drives return visits, upsell to Pro — digest emails every active subscriber weekly (Celery Beat Monday 09:00 UTC), HTML+text; `send_weekly_digest` mgmt command; tests — `core/tests/test_digest.py`

---

## Suggested next steps (highest-impact, unblocked)

1. **Feature 7 — API docs + webhooks**: quick wins (drf-spectacular for Swagger); score-change webhooks can reuse the `core/notifications.py` delivery layer built for alerts.
2. **Feature 1 — finish tiers**: plumb Free/Pro/Enterprise into `Subscription` + permissions, add API keys + rate limiting.
3. **Feature 6 — Screener**: extend existing asset-list filtering into a saveable screener.
4. **Feature 8/9 — Telegram bot + weekly digest**: alerts already have the Telegram send primitive; a bot (`/score`, `/alerts`, `/top`) and email digest build naturally on top.
5. **Feature 5 — Portfolio tracker**: import holdings, aggregated portfolio score, P&L vs predictions.
