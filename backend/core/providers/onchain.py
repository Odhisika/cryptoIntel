"""
CoinGecko on-chain (GeckoTerminal) provider — holder count + top-10
concentration via the free Token Info endpoint.

Verified 2026-08-07 against https://docs.coingecko.com/demo/reference/token-info-contract-address
(official CoinGecko docs, "demo" = free-tier reference page). Confirmed:
  - Base URL is the SAME free host as market data: api.coingecko.com
    (NOT pro-api.coingecko.com), auth header x-cg-demo-api-key (same key
    as CoinGeckoProvider, works with no key at all on lower rate limits
    same as the market-data endpoints).
  - Response includes a `holders` object with `count` and
    `distribution_percentage.top_10` (percentage of supply held by the
    top 10 addresses) — genuinely free whale-concentration data, distinct
    from the PAID "Top Token Holders" endpoint (which returns actual
    addresses and requires an Analyst-tier+ subscription, $129/mo+, NOT
    used here).
  - CoinGecko's own docs flag holders data as "Beta, with ongoing
    improvements to coverage and update frequency" — coverage gaps are
    expected and handled as insufficient_data, not errors.

IMPORTANT CAVEAT: this was verified against CoinGecko's documentation
pages, not a live API call — this sandboxed build environment's network
egress is restricted to package registries, not general APIs. The field
names and nesting above are taken directly from CoinGecko's own published
example response, but should still get one live smoke-test call in an
environment with real network access (e.g. Claude Code) before this is
trusted in production, per the same discipline applied to every other
provider in this codebase.

Network ID mapping: GeckoTerminal's onchain "network" id differs from
CoinGecko's asset_platform id used in /coins/{id}'s `platforms` field
(e.g. asset_platform "ethereum" -> onchain network "eth"). The mapping
below covers major chains only; unmapped platforms are skipped with a
warning rather than guessed at. Extend via GET /onchain/networks if a
needed chain is missing.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import requests

from .base import OnChainProvider, ProviderError

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"

# CoinGecko asset_platform_id -> GeckoTerminal onchain network id.
# Incomplete by design — see module docstring.
ASSET_PLATFORM_TO_ONCHAIN_NETWORK = {
    "ethereum": "eth",
    "binance-smart-chain": "bsc",
    "polygon-pos": "polygon_pos",
    "solana": "solana",
    "arbitrum-one": "arbitrum",
    "optimistic-ethereum": "optimism",
    "base": "base",
    "avalanche": "avax",
    "fantom": "ftm",
}


@dataclass(frozen=True)
class HolderData:
    network: str
    address: str
    holder_count: Optional[int]
    top_10_concentration_pct: Optional[Decimal]
    observed_at: datetime
    source: str


class CoinGeckoOnChainProvider(OnChainProvider):
    name = "coingecko_onchain"

    def __init__(self, *, api_key: Optional[str] = None, timeout: int = 15, max_retries: int = 3):
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = requests.Session()

    def _get(self, path: str) -> dict:
        url = f"{COINGECKO_BASE_URL}{path}"
        headers = {"x-cg-demo-api-key": self.api_key} if self.api_key else {}

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(url, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(min(2 ** attempt, 10))
                continue

            if resp.status_code == 429:
                time.sleep(min(int(resp.headers.get("Retry-After", 2 ** attempt)), 30))
                continue
            if resp.status_code >= 500:
                last_error = ProviderError(self.name, f"HTTP {resp.status_code}", retryable=True)
                time.sleep(min(2 ** attempt, 10))
                continue
            if resp.status_code >= 400:
                raise ProviderError(self.name, f"HTTP {resp.status_code}: {resp.text[:200]}", retryable=False)

            return resp.json()

        raise ProviderError(self.name, f"Exhausted {self.max_retries} retries: {last_error}", retryable=True)

    def fetch_holder_data(self, asset_platform_id: str, contract_address: str) -> HolderData:
        network = ASSET_PLATFORM_TO_ONCHAIN_NETWORK.get(asset_platform_id)
        if network is None:
            raise ProviderError(
                self.name,
                f"No onchain network mapping for asset_platform '{asset_platform_id}' — "
                f"extend ASSET_PLATFORM_TO_ONCHAIN_NETWORK",
                retryable=False,
            )

        payload = self._get(f"/onchain/networks/{network}/tokens/{contract_address}/info")
        attributes = (payload.get("data") or {}).get("attributes") or {}
        holders = attributes.get("holders")

        if not holders:
            # Coverage gap — CoinGecko's own docs describe this data as
            # Beta with incomplete coverage. Not every token will have it.
            return HolderData(
                network=network, address=contract_address, holder_count=None,
                top_10_concentration_pct=None, observed_at=datetime.now(timezone.utc), source=self.name,
            )

        count = holders.get("count")
        top_10_raw = (holders.get("distribution_percentage") or {}).get("top_10")

        return HolderData(
            network=network,
            address=contract_address,
            holder_count=int(count) if count is not None else None,
            top_10_concentration_pct=Decimal(str(top_10_raw)) if top_10_raw is not None else None,
            observed_at=datetime.now(timezone.utc),
            source=self.name,
        )

    def fetch_holder_distribution(self, contract_address: str, chain: str) -> dict:
        """Satisfies the OnChainProvider ABC. `chain` here is expected to
        be a CoinGecko asset_platform_id (e.g. "ethereum"), matching
        Blockchain.slug's convention in this codebase."""
        data = self.fetch_holder_data(chain, contract_address)
        return {
            "contract_address": data.address,
            "chain": chain,
            "holders": data.holder_count,
            "top_10_concentration_pct": data.top_10_concentration_pct,
            "observed_at": data.observed_at,
            "source": data.source,
            "mocked": False,
        }
