"""
DEX Screener implementation of DEXPairProvider.

Provides DEX pair data: liquidity, volume, buy/sell activity, token age,
price changes across multiple timeframes. This is the primary data source
for detecting new gems before they appear on CoinGecko's radar.

API: https://api.dexscreener.com (public, no key required).
Rate limit: documented up to 300 req/min for token/pair endpoints;
we stay conservative at ~10 req/min.

Last verified 2026-08-20 against https://docs.dexscreener.com/api/reference
— re-check before commercial launch; see docs/DATA_LICENSING.md.

DEX Screener returns per-PAIR data, not per-token. A single token can
have multiple pairs across different DEXes and chains. This provider
returns raw pair data; aggregation at the token level is the caller's
responsibility (see core/tasks/dex_ingestion.py).
"""

import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

import requests

from .base import DEXPairData, DEXPairProvider, ProviderError

DEXSCREENER_BASE_URL = "https://api.dexscreener.com"

# Chain name normalization — DEX Screener uses its own chain identifiers
# which sometimes differ from ours. This mapping covers the major chains
# our universe is likely to encounter. Extend as needed.
_CHAIN_NORMALIZATION = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "solana": "solana",
    "sol": "solana",
    "base": "base",
    "bsc": "bsc",
    "binance-smart-chain": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "fantom": "fantom",
    "ftm": "fantom",
    "optimism": "optimism",
    "op": "optimism",
    "cronos": "cronos",
    "gnosis": "gnosis",
    "xdai": "gnosis",
}


def _normalize_chain(chain: str) -> str:
    return _CHAIN_NORMALIZATION.get(chain.lower().strip(), chain.lower().strip())


def _safe_decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _safe_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _parse_ts_ms(ts_ms) -> Optional[datetime]:
    """Convert millisecond timestamp to datetime, or None."""
    if ts_ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


class DEXScreenerProvider(DEXPairProvider):
    name = "dexscreener"

    def __init__(self, *, timeout: int = 15, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = requests.Session()

    def _get(self, path: str, params: Optional[dict] = None) -> dict | list:
        url = f"{DEXSCREENER_BASE_URL}{path}"
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(min(2 ** attempt, 10))
                continue

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
                time.sleep(min(retry_after, 30))
                continue

            if resp.status_code >= 500:
                last_error = ProviderError(self.name, f"HTTP {resp.status_code}", retryable=True)
                time.sleep(min(2 ** attempt, 10))
                continue

            if resp.status_code >= 400:
                raise ProviderError(
                    self.name, f"HTTP {resp.status_code}: {resp.text[:200]}", retryable=False
                )

            return resp.json()

        raise ProviderError(
            self.name, f"Exhausted {self.max_retries} retries: {last_error}", retryable=True
        )

    def fetch_pairs_by_tokens(self, token_addresses: list[dict]) -> list[DEXPairData]:
        """Fetch DEX pair data for tokens, batched by chain.

        token_addresses format: [{"address": "0x...", "chain": "ethereum"}, ...]
        DEX Screener's /tokens/v1/{chain}/{addresses} endpoint takes up to
        30 comma-separated addresses per call.
        """
        if not token_addresses:
            return []

        now = datetime.now(timezone.utc)
        results: list[DEXPairData] = []

        # Group by chain
        by_chain: dict[str, list[str]] = {}
        for item in token_addresses:
            chain = _normalize_chain(item["chain"])
            addr = item["address"].lower()
            by_chain.setdefault(chain, []).append(addr)

        batch_size = 30  # DEX Screener limit per request
        for chain, addresses in by_chain.items():
            for i in range(0, len(addresses), batch_size):
                batch = addresses[i:i + batch_size]
                addr_str = ",".join(batch)
                try:
                    payload = self._get(f"/tokens/v1/{chain}/{addr_str}")
                except ProviderError:
                    # If this chain/endpoint isn't supported, skip silently
                    # rather than failing the whole ingestion — DEX Screener
                    # doesn't cover every chain.
                    continue

                if not isinstance(payload, list):
                    continue

                for row in payload:
                    pair = self._parse_pair(row, now)
                    if pair is not None:
                        results.append(pair)

        return results

    def fetch_trending_pairs(self) -> list[DEXPairData]:
        """GET /token-profiles/latest/v1 — latest token profiles, which
        serves as a proxy for trending/new tokens. Returns profiles, not
        full pair data — caller should cross-reference with
        fetch_pairs_by_tokens for complete metrics."""
        now = datetime.now(timezone.utc)
        payload = self._get("/token-profiles/latest/v1")
        if not isinstance(payload, list):
            return []

        results: list[DEXPairData] = []
        for row in payload:
            chain = _normalize_chain(row.get("chainId", ""))
            token_addr = (row.get("tokenAddress") or "").lower()
            if not chain or not token_addr:
                continue

            # Token profiles don't include pair metrics; return a minimal
            # DEXPairData with just identity — caller must enrich via
            # fetch_pairs_by_tokens for real scoring data.
            results.append(DEXPairData(
                chain=chain,
                dex_name="unknown",
                pair_address="",
                base_token_address=token_addr,
                base_token_symbol=row.get("description", "")[:20],
                quote_token_symbol="",
                price_usd=Decimal("0"),
                fdv_usd=None,
                market_cap_usd=None,
                liquidity_usd=Decimal("0"),
                volume_24h_usd=None,
                volume_6h_usd=None,
                volume_1h_usd=None,
                price_change_24h_pct=None,
                price_change_6h_pct=None,
                price_change_1h_pct=None,
                txns_24h_buys=None,
                txns_24h_sells=None,
                pair_created_at=None,
                observed_at=now,
                source=self.name,
            ))

        return results

    def _parse_pair(self, row: dict, observed_at: datetime) -> Optional[DEXPairData]:
        """Parse one pair object from DEX Screener's response into a
        normalized DEXPairData. Returns None for unparseable rows rather
        than raising, since DEX Screener returns heterogeneous data across
        chains/DEXes."""
        chain = _normalize_chain(row.get("chainId", ""))
        pair_address = row.get("pairAddress", "")
        if not chain or not pair_address:
            return None

        base_token = row.get("baseToken") or {}
        quote_token = row.get("quoteToken") or {}

        price_usd = _safe_decimal(row.get("priceUsd"))
        if price_usd is None:
            return None

        txns = row.get("txns") or {}
        txns_24h = txns.get("h24") or {}

        # Volume can be at top level or nested
        volume = row.get("volume") or {}
        price_change = row.get("priceChange") or {}

        pair_created = _parse_ts_ms(row.get("pairCreatedAt"))

        return DEXPairData(
            chain=chain,
            dex_name=(row.get("dexId") or "unknown"),
            pair_address=pair_address,
            base_token_address=(base_token.get("address") or "").lower() or None,
            base_token_symbol=base_token.get("symbol", ""),
            quote_token_symbol=quote_token.get("symbol", ""),
            price_usd=price_usd,
            fdv_usd=_safe_decimal(row.get("fdv")),
            market_cap_usd=_safe_decimal(row.get("marketCap")),
            liquidity_usd=_safe_decimal(row.get("liquidity", {}).get("usd")) or Decimal("0"),
            volume_24h_usd=_safe_decimal(volume.get("h24")),
            volume_6h_usd=_safe_decimal(volume.get("h6")),
            volume_1h_usd=_safe_decimal(volume.get("h1")),
            price_change_24h_pct=_safe_decimal(price_change.get("h24")),
            price_change_6h_pct=_safe_decimal(price_change.get("h6")),
            price_change_1h_pct=_safe_decimal(price_change.get("h1")),
            txns_24h_buys=_safe_int(txns_24h.get("buys")),
            txns_24h_sells=_safe_int(txns_24h.get("sells")),
            pair_created_at=pair_created,
            observed_at=observed_at,
            source=self.name,
        )
