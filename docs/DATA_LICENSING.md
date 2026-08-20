# Data Licensing Register

Every provider integrated into this product must have an entry here **before**
its data is used in anything user-facing. Update "Last Verified" whenever
terms are re-checked — providers change pricing/terms without much notice.

## CoinGecko (Public API, free tier)

| Field | Value |
|---|---|
| Provider | CoinGecko |
| Data used | Price, market cap, FDV, 24h volume, circulating/total/max supply |
| API | `https://api.coingecko.com/api/v3` (public, no key required for this tier) |
| License | CoinGecko Public API Terms — **needs re-verification against the live terms page before commercial/public launch.** As of last check, the free "Public API" (as opposed to "Pro API") has restrictions on commercial use and requires attribution; this has not been re-confirmed for this specific product's use case. |
| Commercial permission | **UNCONFIRMED — do not launch publicly on the free tier without re-checking.** |
| Redistribution permission | Unconfirmed |
| Attribution requirement | CoinGecko generally requires "Powered by CoinGecko" attribution where their data is displayed — needs to be added to any public-facing page using this data (Phase 11), and confirmed against current terms. |
| Rate limit | ~10–30 calls/min on the free public tier (varies; CoinGecko has changed this before). Handled in code via retry/backoff in `CoinGeckoProvider`. |
| Cost | Free tier: $0. Paid "Analyst"/"Pro" tiers exist with higher limits and different commercial terms. |
| Last verified | Not yet independently verified against live CoinGecko terms as part of this build — **action item before Phase 11 (public dashboard) ships.** |

## DefiLlama (public API, TVL only)

| Field | Value |
|---|---|
| Provider | DefiLlama |
| Data used | Protocol TVL (current + 1D/7D change), protocol list with gecko_id for identity resolution |
| API | `https://api.llama.fi` (public, no key required) |
| License | **UNCONFIRMED — needs re-verification against DefiLlama's current terms (https://defillama.com/docs/api) before commercial/public launch.** DefiLlama is widely used as an open data source; exact commercial-use and redistribution terms have not been independently re-checked as part of this build. |
| Commercial permission | Unconfirmed |
| Redistribution permission | Unconfirmed |
| Attribution requirement | DefiLlama is commonly attributed as "Data from DefiLlama" where TVL figures are displayed — needs to be added to any public-facing page using this data (Phase 11) and confirmed against current terms. |
| Rate limit | Not formally published for the free public API as of last check — implemented conservative retry/backoff and an hourly (not per-minute) ingestion cadence to stay well within reasonable use. |
| Cost | Free tier: $0. Pro API exists for higher-volume/commercial use — not evaluated yet. |
| Last verified | Not yet independently verified against live DefiLlama terms — **action item before Phase 11 (public dashboard) ships**, same as CoinGecko above. |
| Scope note | TVL (Phase 3.1) and fees/revenue via `/summary/fees/{protocol}` (Phase 3.1b) are integrated. Verified against the official API docs at https://api-docs.defillama.com/ on 2026-08-07: both are free, no-auth endpoints on `api.llama.fi`. User-activity/count data is not published on the free tier — still unimplemented. |

## CoinGecko On-Chain (GeckoTerminal Token Info — free tier)

| Field | Value |
|---|---|
| Provider | CoinGecko (on-chain/GeckoTerminal data, same company as the market-data provider above) |
| Data used | Holder count, top-10-address concentration percentage |
| API | `https://api.coingecko.com/api/v3/onchain/networks/{network}/tokens/{address}/info` — same free host as market data, `x-cg-demo-api-key` header (same key as `COINGECKO_API_KEY`) |
| License | Same CoinGecko terms as the market-data entry above — **unconfirmed for commercial use**, same action item. |
| Commercial permission | Unconfirmed (same as CoinGecko market data) |
| Redistribution permission | Unconfirmed |
| Attribution requirement | Same as CoinGecko market data — needs confirming and adding before public launch. |
| Rate limit | Same free-tier limits as CoinGecko market data. |
| Cost | Free tier: $0. **Important distinction**: this "Token Info" endpoint (holder count + top-10 concentration %) is free; CoinGecko's separate "Top Token Holders" endpoint (actual addresses, top 50) requires Analyst tier or above ($129/mo+) and is **not used anywhere in this codebase**. |
| Last verified | Verified against official docs (`docs.coingecko.com/demo/reference/token-info-contract-address`) on 2026-08-07, including the exact response shape. **Not verified against a live API call** — this build environment's network access is restricted to package registries. Recommend one live smoke-test call before trusting in production. |
| Coverage note | CoinGecko's own docs describe holders data as "Beta, with ongoing improvements to coverage and update frequency" — expect gaps, especially for smaller/newer tokens. Handled as `insufficient_data`, not as errors. |

## Template for future providers

```
| Field | Value |
|---|---|
| Provider | |
| Data used | |
| API | |
| License | |
| Commercial permission | |
| Redistribution permission | |
| Attribution requirement | |
| Rate limit | |
| Cost | |
| Last verified | |
```

## GitHub API (public REST API, free)

| Field | Value |
|---|---|
| Provider | GitHub |
| Data used | Repo stars/forks/open issues/archived status/last push time, recent commit activity |
| API | `https://api.github.com` — public REST API |
| License | GitHub's standard API Terms of Service — considerably more permissive and standard than the crypto-data providers above, but commercial-use confirmation is still an open action item for consistency with every other provider in this doc. |
| Commercial permission | Not yet explicitly confirmed for this product's use case, though GitHub's terms are well-established and widely relied upon for exactly this kind of read-only public-repo usage. |
| Redistribution permission | Not applicable — only derived scores are stored/displayed, not raw GitHub content. |
| Attribution requirement | None required for this kind of API usage. |
| Rate limit | **60/hr unauthenticated** (per IP) — confirmed live during this build (this sandbox's shared egress IP had already exhausted it before this chunk started). **5,000/hr with a free personal access token** (`GITHUB_TOKEN` env var, no special scopes needed for public repo reads) — strongly recommended for ingesting more than a handful of assets. |
| Cost | Free. |
| Last verified | **Partially live-verified** on 2026-08-09 — repo-level fields (stars, forks, open_issues, pushed_at, archived) confirmed against a REAL response from `api.github.com` (via the `/search/repositories` endpoint, which shares schema with `/repos/{owner}/{repo}` but draws from a separate rate-limit bucket). The `/stats/commit_activity` endpoint was NOT live-verified this session (core quota was already exhausted) — taken from GitHub's own long-stable published docs instead. See `PHASE_6_NOTES.md` for the verification transcript. |
| Coverage note | Requires `Asset.github_repo_url` to be populated first (via `populate_github_repos`, sourced from CoinGecko's `links.repos_url.github` field) — not every asset has a linked repo, and multi-repo projects only get their first-listed repo tracked. |

## Providers evaluated and NOT integrated (paid-only, confirmed)

- **DefiLlama `/api/emissions` and `/api/emission/{protocol}`** (token
  unlock schedules) — confirmed Pro-only ($300/mo) against
  `https://api-docs.defillama.com/` on 2026-08-08. The free `/protocols`
  and `/protocol/{slug}` endpoints (TVL) remain free and ARE integrated —
  only the emissions/unlocks endpoints are paid.
- **Tokenomist.ai API** (unlock events, allocations, daily emissions) —
  confirmed to require a paid API key (`x-api-key` header, "Get Free
  Trial API Now" — a trial, not a permanently free tier) against
  `docs.tokenomist.ai` on 2026-08-08.
- **Net effect**: `token_unlock_risk` in the Risk Score, and the
  team/investor/foundation allocation breakdown in section 18 of the
  master spec, have no free data source as of this check. See
  `core/scoring/tokenomics_math.py`'s module docstring for what WAS built
  instead from data already on hand (circulating/max supply ratios,
  realized inflation) with no new provider needed.
- **CoinMarketCal** (crypto events/catalysts, section 23) — confirmed
  against their own pricing page (fetched 2026-08-09): the free
  "Personal" tier is explicitly **personal-use licensed**, plus limited
  to 7 days of upcoming events with no past-event access. An even
  stronger disqualifier than "commercial terms unconfirmed" — this one is
  explicit. Paid tiers start at $49.80/mo.
- **LunarCrush API** (social sentiment, section 25) — confirmed the API
  itself is pay-as-you-go with no free tier (their free account level is
  for the website dashboard only, not programmatic access).
- **Net effect**: `core.models.Catalyst` (Phase 8) is populated ONLY via
  manual, sourced curation (`add_catalyst` command) — no automated feed.
  `SocialDataProvider` remains mock-only; section 25's social sentiment
  metrics (attention, engagement, bot likelihood, influencer
  concentration) are entirely unaddressed. See `PHASE_8_NOTES.md`.

## Providers referenced in the spec but not yet integrated

DefiLlama, Tokenomist, GitHub, blockchain RPC providers, Dune, Glassnode,
Artemis, Santiment, CryptoQuant — none of these have been contracted,
verified, or integrated yet. Mock implementations exist in
`core/providers/mocks.py` so downstream code can be built against the
interfaces now. **Do not treat mock provider output as real data anywhere
in scoring, UI, or reports** — every mock response includes a `mocked: True`
flag specifically so this can't happen silently.

## DEX Screener (public API, DEX pair data)

| Field | Value |
|---|---|
| Provider | DEX Screener |
| Data used | DEX pair data: liquidity, volume (24h/6h/1h), price changes, buy/sell transaction counts, pair creation time (token age), FDV, market cap, chain, DEX name |
| API | `https://api.dexscreener.com` (public, no key required) |
| License | **UNCONFIRMED — needs re-verification against DEX Screener's current terms before commercial/public launch.** DEX Screener is widely used as an open data source; exact commercial-use terms have not been independently verified as part of this build. |
| Commercial permission | Unconfirmed |
| Redistribution permission | Unconfirmed |
| Attribution requirement | DEX Screener is commonly attributed as "Data from DEX Screener" — needs to be added to any public-facing page using this data (Phase 11) and confirmed against current terms. |
| Rate limit | Documented up to 300 req/min for token/pair endpoints. Implemented conservative rate limiting (~10 req/min) with retry/backoff. |
| Cost | Free tier: $0. Pro API exists for higher volume — not evaluated. |
| Last verified | 2026-08-20 against https://docs.dexscreener.com/api/reference — endpoints confirmed available without authentication. Not yet live-test-verified from this build environment. |
| Scope note | DEX Screener returns per-PAIR data, not per-token. A token can have multiple pairs across DEXes/chains. Aggregation at the token level (summed liquidity/volume, earliest pair creation time) is performed in `core/tasks/dex_ingestion.py`. |

## Binance (public market data API)

| Field | Value |
|---|---|
| Provider | Binance |
| Data used | BTC/ETH daily candles (OHLCV), 24h ticker statistics, all-USDT-pair tickers (for BTC volume dominance approximation) |
| API | `https://api.binance.com/api/v3` — public market data endpoints only, no authentication required |
| License | Binance's standard API Terms of Service — public market data endpoints are explicitly unauthenticated per Binance's own FAQ (https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md). Commercial-use terms are more permissive than crypto-data aggregators but should be confirmed before public launch. |
| Commercial permission | Public market data endpoints are explicitly designed for unauthenticated access; commercial use of market data is standard. Re-confirmation recommended for consistency. |
| Redistribution permission | Not applicable — only derived regime indicators are stored/displayed, not raw Binance data. |
| Attribution requirement | None required for public market data endpoints. |
| Rate limit | 1,200 request weight per minute for general endpoints. Our usage (~6 calls per ingestion cycle every 15 min) is well under this. |
| Cost | Free for public market data endpoints. |
| Last verified | 2026-08-20 against Binance's public API docs and the market_data_only FAQ. |
| Scope note | Used exclusively for market regime analysis (BTC/ETH trend, volume dominance, ETH/BTC ratio) — NOT as a replacement for CoinGecko for general asset price/MC/volume data. BTC "dominance" is volume-based (BTC USDT quote volume / total USDT quote volume), NOT market-cap-based — documented limitation in `core/providers/binance.py`. |
