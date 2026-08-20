"""
Provider abstraction layer.

Nothing downstream (ingestion jobs, scoring engine) may import a concrete
provider (CoinGeckoProvider, DefiLlamaProvider, etc.) directly. Everything
talks to these interfaces so providers can be added/swapped without
touching the scoring engine. See docs/DATA_LICENSING.md before adding any
new provider implementation.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional


class ProviderError(Exception):
    """Raised on any provider failure (network, auth, rate limit, bad payload)."""

    def __init__(self, provider: str, message: str, *, retryable: bool = True):
        self.provider = provider
        self.retryable = retryable
        super().__init__(f"[{provider}] {message}")


@dataclass(frozen=True)
class MarketSnapshotData:
    """Normalized market data point. Providers translate their own payload
    shape into this before it ever reaches ingestion/storage code."""

    external_id: str  # provider's id for the asset, e.g. coingecko "bitcoin"
    symbol: str
    name: str
    price_usd: Decimal
    market_cap_usd: Optional[Decimal]
    fully_diluted_valuation_usd: Optional[Decimal]
    volume_24h_usd: Optional[Decimal]
    circulating_supply: Optional[Decimal]
    total_supply: Optional[Decimal]
    max_supply: Optional[Decimal]
    observed_at: datetime
    source: str  # provider name, for auditability


class MarketDataProvider(ABC):
    """Price / market cap / volume / supply data."""

    name: str

    @abstractmethod
    def fetch_market_snapshot(self, external_ids: list[str]) -> list[MarketSnapshotData]:
        """Fetch current market data for the given provider-native asset ids.
        Must raise ProviderError (not a bare exception) on any failure so
        ingestion jobs can distinguish retryable vs fatal errors."""
        raise NotImplementedError

    @abstractmethod
    def list_universe(self, *, min_market_cap: Decimal, max_market_cap: Decimal) -> list[str]:
        """Return provider-native ids of assets within a market cap band,
        used to build the scannable universe (see section 12 of the spec)."""
        raise NotImplementedError


class DefiDataProvider(ABC):
    name: str

    @abstractmethod
    def fetch_protocol_metrics(self, protocol_id: str) -> dict:
        """TVL, fees, revenue for a protocol. Shape TBD in Phase 3 — kept as
        dict for now since we haven't ingested a real payload yet; this will
        become a dataclass like MarketSnapshotData once Phase 3 starts."""
        raise NotImplementedError


class OnChainProvider(ABC):
    name: str

    @abstractmethod
    def fetch_holder_distribution(self, contract_address: str, chain: str) -> dict:
        raise NotImplementedError


class TokenomicsProvider(ABC):
    name: str

    @abstractmethod
    def fetch_unlock_schedule(self, external_id: str) -> dict:
        raise NotImplementedError


class DeveloperActivityProvider(ABC):
    name: str

    @abstractmethod
    def fetch_repo_activity(self, repo_url: str) -> dict:
        raise NotImplementedError


class SocialDataProvider(ABC):
    name: str

    @abstractmethod
    def fetch_social_metrics(self, external_id: str) -> dict:
        raise NotImplementedError


class NewsProvider(ABC):
    name: str

    @abstractmethod
    def fetch_recent_events(self, external_id: str) -> list[dict]:
        raise NotImplementedError


@dataclass(frozen=True)
class DEXPairData:
    """Normalized DEX pair data point. DEX Screener returns per-pair data;
    this represents either a single pair or an aggregation of multiple
    pairs for the same token across DEXes/chains."""

    chain: str
    dex_name: str
    pair_address: str
    base_token_address: Optional[str]
    base_token_symbol: str
    quote_token_symbol: str
    price_usd: Decimal
    fdv_usd: Optional[Decimal]
    market_cap_usd: Optional[Decimal]
    liquidity_usd: Decimal
    volume_24h_usd: Optional[Decimal]
    volume_6h_usd: Optional[Decimal]
    volume_1h_usd: Optional[Decimal]
    price_change_24h_pct: Optional[Decimal]
    price_change_6h_pct: Optional[Decimal]
    price_change_1h_pct: Optional[Decimal]
    txns_24h_buys: Optional[int]
    txns_24h_sells: Optional[int]
    pair_created_at: Optional[datetime]
    observed_at: datetime
    source: str


class DEXPairProvider(ABC):
    """DEX pair data — liquidity, volume, buy/sell activity, token age.
    Used for gem detection where CoinGecko alone is insufficient."""
    name: str

    @abstractmethod
    def fetch_pairs_by_tokens(self, token_addresses: list[dict]) -> list[DEXPairData]:
        """Fetch DEX pair data for the given tokens.
        token_addresses = [{"address": "0x...", "chain": "ethereum"}, ...]
        Must raise ProviderError on failure."""
        raise NotImplementedError

    @abstractmethod
    def fetch_trending_pairs(self) -> list[DEXPairData]:
        """Fetch trending/newest DEX pairs across chains."""
        raise NotImplementedError


@dataclass(frozen=True)
class CandleData:
    """OHLCV candle from an exchange."""
    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    close_time: datetime


@dataclass(frozen=True)
class TickerData:
    """24h ticker summary from an exchange."""
    symbol: str
    price_usd: Decimal
    volume_24h_usd: Optional[Decimal]
    price_change_pct: Optional[Decimal]
    quote_volume_24h_usd: Optional[Decimal]
    trades_count: Optional[int]
    observed_at: datetime


class MarketRegimeProvider(ABC):
    """Market regime data — BTC/ETH trends, dominance, relative strength.
    Used for market-context scoring adjustments."""
    name: str

    @abstractmethod
    def fetch_candles(self, symbol: str, interval: str, limit: int) -> list[CandleData]:
        raise NotImplementedError

    @abstractmethod
    def fetch_24h_ticker(self, symbol: str) -> TickerData:
        raise NotImplementedError

    @abstractmethod
    def fetch_all_usdt_tickers(self) -> list[TickerData]:
        """Fetch 24h tickers for all USDT pairs (for dominance calc)."""
        raise NotImplementedError
