"""
DefiLlama implementation of DefiDataProvider — TVL only for Phase 3.1.

Fees/revenue require DefiLlama's separate /overview/fees and
/overview/revenue endpoints with different protocol-slug matching
semantics; deliberately deferred to a follow-up chunk (3.1b) rather than
bolted on here, so this chunk stays reviewable as one thing.

API: https://api.llama.fi (public, no key required). Last verified
2026-08-06 against DefiLlama's public docs at https://defillama.com/docs/api
— re-check before relying on this for a commercial launch; see
docs/DATA_LICENSING.md.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import requests

from .base import DefiDataProvider, ProviderError

DEFILLAMA_BASE_URL = "https://api.llama.fi"


@dataclass(frozen=True)
class ProtocolListing:
    """One row from DefiLlama's /protocols list — used for identity
    resolution (matching a DefiLlama protocol to our Asset via gecko_id,
    which DefiLlama publishes precisely so third parties can cross-
    reference against CoinGecko)."""

    slug: str
    name: str
    gecko_id: Optional[str]
    category: Optional[str]
    tvl_usd: Optional[Decimal]
    chains: list[str]


@dataclass(frozen=True)
class ProtocolTVLData:
    slug: str
    tvl_usd: Decimal
    change_1d_pct: Optional[Decimal]
    change_7d_pct: Optional[Decimal]
    observed_at: datetime
    source: str


@dataclass(frozen=True)
class ProtocolFeesData:
    slug: str
    fees_24h_usd: Decimal
    fees_7d_usd: Optional[Decimal]
    fees_30d_usd: Optional[Decimal]
    revenue_24h_usd: Optional[Decimal]  # None = protocol takes no fee cut (real answer, not missing)
    observed_at: datetime
    source: str


class DefiLlamaProvider(DefiDataProvider):
    name = "defillama"

    def __init__(self, *, timeout: int = 20, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = requests.Session()

    def _get(self, path: str) -> dict | list:
        url = f"{DEFILLAMA_BASE_URL}{path}"
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(url, timeout=self.timeout)
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

    def list_protocols(self) -> list[ProtocolListing]:
        """GET /protocols — full protocol list with current TVL + gecko_id
        for identity resolution against Asset.external_ids.coingecko."""
        payload = self._get("/protocols")
        if not isinstance(payload, list):
            raise ProviderError(self.name, f"Unexpected /protocols payload shape: {type(payload)}", retryable=False)

        listings = []
        for row in payload:
            slug = row.get("slug")
            if not slug:
                continue
            tvl = row.get("tvl")
            listings.append(
                ProtocolListing(
                    slug=slug,
                    name=row.get("name", slug),
                    gecko_id=row.get("gecko_id"),
                    category=row.get("category"),
                    tvl_usd=Decimal(str(tvl)) if tvl is not None else None,
                    chains=row.get("chains", []) or [],
                )
            )
        return listings

    def fetch_protocol_tvl(self, slug: str) -> ProtocolTVLData:
        """GET /protocol/{slug} — current TVL + 1D/7D change for one
        protocol. This is the real, typed method; fetch_protocol_metrics
        below wraps it to satisfy the DefiDataProvider abstract interface
        (which still returns dict, to stay compatible with providers that
        aren't fully typed yet)."""
        payload = self._get(f"/protocol/{slug}")
        if not isinstance(payload, dict):
            raise ProviderError(self.name, f"Unexpected /protocol/{slug} payload shape", retryable=False)

        tvl = payload.get("tvl")
        if not isinstance(tvl, list) or not tvl:
            raise ProviderError(self.name, f"No TVL history for protocol '{slug}'", retryable=False)

        latest = tvl[-1]
        current_tvl = Decimal(str(latest["totalLiquidityUSD"]))

        def _pct_change_over(days: int) -> Optional[Decimal]:
            target_ts = latest["date"] - days * 86400
            # tvl entries are chronological; find the closest one to the
            # target timestamp within a day of tolerance.
            best = min(tvl, key=lambda e: abs(e["date"] - target_ts))
            if abs(best["date"] - target_ts) > 86400 * 1.5:
                return None
            baseline = Decimal(str(best["totalLiquidityUSD"]))
            if baseline <= 0:
                return None
            return ((current_tvl - baseline) / baseline) * Decimal("100")

        return ProtocolTVLData(
            slug=slug,
            tvl_usd=current_tvl,
            change_1d_pct=_pct_change_over(1),
            change_7d_pct=_pct_change_over(7),
            observed_at=datetime.fromtimestamp(latest["date"], tz=timezone.utc),
            source=self.name,
        )

    def fetch_protocol_metrics(self, protocol_id: str) -> dict:
        """Satisfies the DefiDataProvider ABC. protocol_id is the
        DefiLlama slug here (e.g. "uniswap")."""
        data = self.fetch_protocol_tvl(protocol_id)
        return {
            "protocol_id": data.slug,
            "tvl_usd": data.tvl_usd,
            "tvl_change_1d_pct": data.change_1d_pct,
            "tvl_change_7d_pct": data.change_7d_pct,
            "observed_at": data.observed_at,
            "source": data.source,
            "mocked": False,
        }

    def fetch_protocol_fees(self, slug: str) -> ProtocolFeesData:
        """GET /summary/fees/{protocol} — free, no-auth endpoint (verified
        2026-08-07 against https://api-docs.defillama.com/). Returns
        24h/7d/30d fee totals and, where the protocol takes a cut,
        dailyRevenue. Many protocols (pure DEXs paying 100% of fees to
        LPs) have `dailyRevenue: null` — that's a real "protocol takes no
        cut" answer, not missing data, but we still can't compute
        revenue_to_market_cap for them, so it's treated as
        insufficient_data by the scoring layer either way."""
        payload = self._get(f"/summary/fees/{slug}")
        if not isinstance(payload, dict):
            raise ProviderError(self.name, f"Unexpected /summary/fees/{slug} payload shape", retryable=False)

        def _dec(value) -> Optional[Decimal]:
            return None if value is None else Decimal(str(value))

        return ProtocolFeesData(
            slug=slug,
            fees_24h_usd=_dec(payload.get("total24h")) or Decimal("0"),
            fees_7d_usd=_dec(payload.get("total7d")),
            fees_30d_usd=_dec(payload.get("total30d")),
            revenue_24h_usd=_dec(payload.get("dailyRevenue")),
            observed_at=datetime.now(timezone.utc),
            source=self.name,
        )
