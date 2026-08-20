"""
Binance implementation of MarketRegimeProvider.

Provides BTC/ETH market data for regime analysis — trend direction,
relative strength, volume dominance. All endpoints are public market-data
endpoints that require NO authentication.

API: https://api.binance.com — public market data endpoints only.
Binance explicitly supports unauthenticated access for market data:
https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md

Rate limits: 1200 requests/min weight for general endpoints; we stay
well under with ~6 requests per ingestion cycle (2 candle calls + 2
ticker calls + 1 all-tickers call).

Last verified 2026-08-20 against Binance's public API docs.

IMPORTANT SCOPE NOTE: This provider is NOT a replacement for CoinGecko
for general market data. It serves one specific purpose: providing the
market-regime context (BTC/ETH trend, dominance proxy, relative strength)
that the scoring engine needs. For general asset price/MC/volume data,
CoinGecko remains the primary source.
"""

import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional

import requests

from .base import CandleData, MarketRegimeProvider, ProviderError, TickerData

BINANCE_BASE_URL = "https://api.binance.com"

# USDT pairs we care about for regime analysis — extended list for
# dominance calculation (volume share of BTC vs total USDT volume).
REGIME_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]


def _safe_decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


class BinanceProvider(MarketRegimeProvider):
    name = "binance"

    def __init__(self, *, timeout: int = 15, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = requests.Session()

    def _get(self, path: str, params: Optional[dict] = None) -> dict | list:
        url = f"{BINANCE_BASE_URL}{path}"
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

    def fetch_candles(self, symbol: str, interval: str = "1d", limit: int = 100) -> list[CandleData]:
        """GET /api/v3/klines — returns OHLCV candle data.

        interval: "1m", "5m", "1h", "4h", "1d", "1w", etc.
        limit: max 1000, we default to 100 (100 days for daily candles).
        """
        payload = self._get("/api/v3/klines", {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": min(limit, 1000),
        })
        if not isinstance(payload, list):
            raise ProviderError(self.name, f"Unexpected klines payload shape: {type(payload)}", retryable=False)

        now = datetime.now(timezone.utc)
        candles: list[CandleData] = []
        for row in payload:
            # Binance kline format: [open_time, open, high, low, close, volume,
            # close_time, quote_volume, trades, taker_buy_base, taker_buy_quote, ignore]
            if not isinstance(row, list) or len(row) < 6:
                continue

            close_time_ms = row[6] if len(row) > 6 else row[0]
            candles.append(CandleData(
                symbol=symbol.upper(),
                open=_safe_decimal(row[1]) or Decimal("0"),
                high=_safe_decimal(row[2]) or Decimal("0"),
                low=_safe_decimal(row[3]) or Decimal("0"),
                close=_safe_decimal(row[4]) or Decimal("0"),
                volume=_safe_decimal(row[5]) or Decimal("0"),
                close_time=datetime.fromtimestamp(
                    int(close_time_ms) / 1000, tz=timezone.utc
                ),
            ))

        return candles

    def fetch_24h_ticker(self, symbol: str) -> TickerData:
        """GET /api/v3/ticker/24hr — 24h rolling window statistics."""
        payload = self._get("/api/v3/ticker/24hr", {"symbol": symbol.upper()})
        if not isinstance(payload, dict):
            raise ProviderError(self.name, f"Unexpected ticker payload shape: {type(payload)}", retryable=False)

        return TickerData(
            symbol=symbol.upper(),
            price_usd=_safe_decimal(payload.get("lastPrice")) or Decimal("0"),
            volume_24h_usd=_safe_decimal(payload.get("volume")),
            price_change_pct=_safe_decimal(payload.get("priceChangePercent")),
            quote_volume_24h_usd=_safe_decimal(payload.get("quoteVolume")),
            trades_count=_safe_int(payload.get("count")),
            observed_at=datetime.now(timezone.utc),
        )

    def fetch_all_usdt_tickers(self) -> list[TickerData]:
        """GET /api/v3/ticker/24hr (no symbol) — all symbols' 24h stats.
        Used for BTC dominance approximation (BTC quote volume / total
        USDT quote volume across all pairs)."""
        payload = self._get("/api/v3/ticker/24hr")
        if not isinstance(payload, list):
            raise ProviderError(self.name, f"Unexpected all-tickers payload shape: {type(payload)}", retryable=False)

        now = datetime.now(timezone.utc)
        tickers: list[TickerData] = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            symbol = row.get("symbol", "")
            # Only include USDT-quoted pairs for volume dominance calc
            if not symbol.endswith("USDT"):
                continue
            tickers.append(TickerData(
                symbol=symbol,
                price_usd=_safe_decimal(row.get("lastPrice")) or Decimal("0"),
                volume_24h_usd=_safe_decimal(row.get("volume")),
                price_change_pct=_safe_decimal(row.get("priceChangePercent")),
                quote_volume_24h_usd=_safe_decimal(row.get("quoteVolume")),
                trades_count=_safe_int(row.get("count")),
                observed_at=now,
            ))

        return tickers

    def compute_daily_moving_average(self, candles: list[CandleData], period: int) -> Optional[Decimal]:
        """Compute a simple moving average from daily close prices.
        Returns None if insufficient data. This is a 50-day MA by default
        (not a true 200-DMA — documented limitation, sufficient for trend
        direction rather than precise technical analysis)."""
        if len(candles) < period:
            return None

        # candles are chronological (oldest first from Binance)
        recent = candles[-period:]
        closes = [c.close for c in recent if c.close > 0]
        if len(closes) < period:
            return None

        avg = sum(closes) / Decimal(len(closes))
        return avg.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def compute_btc_volume_dominance(self, all_tickers: list[TickerData]) -> Optional[Decimal]:
        """Approximate BTC dominance as BTC's USDT quote volume / total
        USDT quote volume across all pairs. This is a VOLUME-based proxy,
        NOT a market-cap-based dominance figure — documented limitation.

        Returns a percentage (0-100)."""
        total_quote_volume = Decimal("0")
        btc_quote_volume = Decimal("0")

        for ticker in all_tickers:
            qv = ticker.quote_volume_24h_usd or Decimal("0")
            total_quote_volume += qv
            if ticker.symbol == "BTCUSDT":
                btc_quote_volume = qv

        if total_quote_volume <= 0:
            return None

        dominance = (btc_quote_volume / total_quote_volume) * Decimal("100")
        return dominance.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _safe_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None
