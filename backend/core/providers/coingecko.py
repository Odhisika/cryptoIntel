"""
CoinGecko implementation of MarketDataProvider.

Free tier constraints (verify against https://www.coingecko.com/en/api/pricing
before relying on these — last verified 2026-08-06, subject to change):
  - ~10-30 calls/minute depending on current published limits
  - No API key required for the public /api/v3 endpoints
  - Commercial use of the free "Public API" has restrictions — read the
    terms before this product goes public. Log this in DATA_LICENSING.md
    once confirmed; do not ship the free tier to a paying product without
    re-checking.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import requests

from .base import MarketDataProvider, MarketSnapshotData, ProviderError

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"


@dataclass(frozen=True)
class CoinDetailData:
    platforms: dict[str, str] = field(default_factory=dict)
    categories: list[str] = field(default_factory=list)
    github_repos: list[str] = field(default_factory=list)


class CoinGeckoProvider(MarketDataProvider):
    name = "coingecko"

    def __init__(self, *, api_key: Optional[str] = None, timeout: int = 15, max_retries: int = 3):
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = requests.Session()

    def _get(self, path: str, params: dict) -> dict:
        url = f"{COINGECKO_BASE_URL}{path}"
        headers = {}
        if self.api_key:
            headers["x-cg-demo-api-key"] = self.api_key

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(url, params=params, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(min(2 ** attempt, 10))
                continue

            if resp.status_code == 429:
                # Rate limited — respect Retry-After if present, else backoff.
                retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
                time.sleep(min(retry_after, 30))
                continue

            if resp.status_code >= 500:
                last_error = ProviderError(self.name, f"HTTP {resp.status_code}", retryable=True)
                time.sleep(min(2 ** attempt, 10))
                continue

            if resp.status_code >= 400:
                # Client errors (bad id, malformed params) are not retryable.
                raise ProviderError(
                    self.name, f"HTTP {resp.status_code}: {resp.text[:200]}", retryable=False
                )

            return resp.json()

        raise ProviderError(
            self.name, f"Exhausted {self.max_retries} retries: {last_error}", retryable=True
        )

    def fetch_market_snapshot(self, external_ids: list[str]) -> list[MarketSnapshotData]:
        if not external_ids:
            return []

        results: list[MarketSnapshotData] = []
        now = datetime.now(timezone.utc)

        # CoinGecko's /coins/markets endpoint takes a comma-separated id list;
        # batch to stay well under URL length / rate-limit-per-call limits.
        batch_size = 100
        for i in range(0, len(external_ids), batch_size):
            batch = external_ids[i : i + batch_size]
            payload = self._get(
                "/coins/markets",
                {
                    "vs_currency": "usd",
                    "ids": ",".join(batch),
                    "price_change_percentage": "24h",
                },
            )
            if not isinstance(payload, list):
                raise ProviderError(self.name, f"Unexpected payload shape: {type(payload)}", retryable=False)

            for row in payload:
                results.append(self._parse_market_row(row, now))

        return results

    def _parse_market_row(self, row: dict, observed_at: datetime) -> MarketSnapshotData:
        def dec(value) -> Optional[Decimal]:
            return None if value is None else Decimal(str(value))

        try:
            return MarketSnapshotData(
                external_id=row["id"],
                symbol=row["symbol"],
                name=row["name"],
                price_usd=dec(row["current_price"]) or Decimal("0"),
                market_cap_usd=dec(row.get("market_cap")),
                fully_diluted_valuation_usd=dec(row.get("fully_diluted_valuation")),
                volume_24h_usd=dec(row.get("total_volume")),
                circulating_supply=dec(row.get("circulating_supply")),
                total_supply=dec(row.get("total_supply")),
                max_supply=dec(row.get("max_supply")),
                observed_at=observed_at,
                source=self.name,
            )
        except KeyError as exc:
            raise ProviderError(self.name, f"Missing expected field {exc} in row {row.get('id')}", retryable=False)

    def list_universe(self, *, min_market_cap: Decimal, max_market_cap: Decimal) -> list[str]:
        ids: list[str] = []
        page = 1
        while True:
            payload = self._get(
                "/coins/markets",
                {
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": 250,
                    "page": page,
                },
            )
            if not payload:
                break

            in_range_this_page = False
            for row in payload:
                mc = row.get("market_cap")
                if mc is None:
                    continue
                mc = Decimal(str(mc))
                if min_market_cap <= mc <= max_market_cap:
                    ids.append(row["id"])
                    in_range_this_page = True
                elif mc < min_market_cap:
                    # Results are market-cap-desc sorted, so once we're below
                    # the floor we can stop paginating entirely.
                    return ids

            page += 1
            if page > 20:  # hard stop — ~5000 assets, avoid runaway pagination
                break

        return ids

    def fetch_coin_detail(self, coingecko_id: str) -> "CoinDetailData":
        """GET /coins/{id} — single call returning both `platforms`
        (Phase 3.2) and `categories` (Phase 3.3). Both fetch_platforms and
        fetch_categories delegate here so we hit this endpoint once per
        asset, not twice."""
        payload = self._get(
            f"/coins/{coingecko_id}",
            {
                "localization": "false", "tickers": "false", "market_data": "false",
                "community_data": "false", "developer_data": "false",
            },
        )
        if not isinstance(payload, dict):
            raise ProviderError(self.name, f"Unexpected /coins/{coingecko_id} payload shape", retryable=False)

        platforms = payload.get("platforms", {}) or {}
        categories = payload.get("categories", []) or []
        github_repos = ((payload.get("links") or {}).get("repos_url") or {}).get("github") or []
        return CoinDetailData(
            platforms={p: addr for p, addr in platforms.items() if addr},
            categories=[c for c in categories if c],  # drop null/empty entries
            github_repos=[r for r in github_repos if r],
        )

    def fetch_platforms(self, coingecko_id: str) -> dict[str, str]:
        """GET /coins/{id} — returns the `platforms` map:
        {asset_platform_id: contract_address}. Used for identity
        resolution (Phase 3.2) to populate ContractAddress rows so
        on-chain data (holder counts, etc.) can be looked up per chain."""
        return self.fetch_coin_detail(coingecko_id).platforms

    def fetch_categories(self, coingecko_id: str) -> list[str]:
        """GET /coins/{id} — returns CoinGecko's raw category list (e.g.
        ["Layer 1 (L1)", "Smart Contract Platform"]). Used for sector
        classification (Phase 3.3) via mapping against our own controlled
        taxonomy — see core/scoring/sectors.py."""
        return self.fetch_coin_detail(coingecko_id).categories

    def fetch_github_repos(self, coingecko_id: str) -> list[str]:
        """GET /coins/{id} — returns CoinGecko's `links.repos_url.github`
        list (e.g. ["https://github.com/ethereum/go-ethereum"]). Used to
        populate Asset.github_repo_url (Phase 6) for developer-activity
        lookups. A project can list multiple repos; the caller picks the
        first as the "primary" one — good enough for v1, not a claim
        about which repo matters most for multi-repo projects."""
        return self.fetch_coin_detail(coingecko_id).github_repos

