# Crypto Intelligence Platform — Feature Roadmap

## Tier 1 — Makes it sellable

### 1. Authentication + API Key System
- User registration/login (JWT or session-based)
- Stripe integration for payments
- Tiered access: Free / Pro / Enterprise
- Rate limiting per API key
- API key management dashboard

### 2. Real-Time Alerts
- Email alerts via SendGrid/SES when token crosses score thresholds
- Telegram bot integration (webhook-based)
- User-configurable alert rules (score thresholds, market cap changes, volume spikes)
- Alert history log

### 3. Historical Score Accuracy Dashboard
- Backtest scores against actual 30/60/90-day price performance
- Win rate, average return, Sharpe ratio per score tier
- Public-facing accuracy stats (sales pitch)
- "Tokens scored 80+ returned avg X% in 90 days"

---

## Tier 2 — Makes it sticky

### 4. Frontend Dashboard (React/Next.js)
- Score distribution overview
- Rankings page with sorting/filtering
- Individual token deep-dive with price charts
- Market regime indicator (bullish/bearish/neutral)
- Responsive mobile design

### 5. Portfolio Tracker
- Import holdings via wallet address or manual entry
- Aggregated portfolio score
- P&L tracking against score predictions
- Alerts when portfolio average drops below threshold

### 6. Custom Screener Filters
- Filter by sector, score ranges, market cap, DEX liquidity
- Save custom screens
- Share screens with other users
- Pro feature: advanced filters (token age, whale concentration, etc.)

---

## Tier 3 — Growth loop

### 7. Public Score API
- REST + GraphQL endpoints
- B2B pricing (per 1,000 calls)
- API documentation portal (Swagger/Redoc)
- Webhook delivery for score changes

### 8. Telegram Bot
- `/score SOL` — instant score lookup
- `/alerts` — manage alert subscriptions
- `/top` — daily top 10X candidates
- Inline keyboards for quick actions

### 9. Weekly Email Digest
- "Top 10 10X candidates this week"
- Market regime summary
- Biggest score movers (upgrades + downgrades)
- Drives return visits, upsell to Pro
