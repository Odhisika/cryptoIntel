"""
Market regime ingestion task from Binance public data.

Fetches BTC/ETH candle and ticker data, computes market regime indicators
(price trend, 50-day MA, ETH/BTC ratio, BTC volume dominance), classifies
the current regime as BULLISH/BEARISH/NEUTRAL, and writes a
MarketRegimeSnapshot.

Regime classification rules (intentionally simple, not a trading signal):
  BULLISH: BTC above 50DMA AND (BTC 7D return > +5% OR ETH/BTC ratio rising)
  BEARISH: BTC below 50DMA AND BTC 7D return < -5% AND ETH/BTC ratio falling
  NEUTRAL: everything else

These thresholds are placeholders that should be recalibrated once
backtesting (Phase 10) can check whether they actually predict anything.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from celery import shared_task
from django.db import transaction

from core.models import DataIngestionJob, MarketRegimeSnapshot
from core.providers import BinanceProvider, ProviderError

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def ingest_market_regime(self):
    """Fetch BTC/ETH data from Binance, compute regime, write snapshot."""

    job = DataIngestionJob.objects.create(provider="binance", job_type="market_regime")

    provider = BinanceProvider()
    now = datetime.now(timezone.utc)

    try:
        btc_ticker = provider.fetch_24h_ticker("BTCUSDT")
        eth_ticker = provider.fetch_24h_ticker("ETHUSDT")
        btc_candles = provider.fetch_candles("BTCUSDT", interval="1d", limit=100)
        eth_candles = provider.fetch_candles("ETHUSDT", interval="1d", limit=100)
        all_tickers = provider.fetch_all_usdt_tickers()
    except ProviderError as exc:
        job.status = DataIngestionJob.Status.FAILED
        job.error_summary = str(exc)
        job.finished_at = datetime.now(timezone.utc)
        job.save()
        if exc.retryable:
            raise self.retry(exc=exc)
        logger.error("Non-retryable Binance error: %s", exc)
        return {"succeeded": 0, "error": str(exc)}

    # Compute indicators
    btc_50dma = provider.compute_daily_moving_average(btc_candles, 50)
    btc_above_50dma = (
        btc_ticker.price_usd > btc_50dma if btc_50dma else None
    )

    # 7D return: compare current price to the close ~7 candles ago
    btc_change_7d = _compute_7d_return(btc_candles)
    eth_change_7d = _compute_7d_return(eth_candles)

    # ETH/BTC ratio and its 7D change
    eth_btc_ratio = None
    eth_btc_change_7d = None
    if btc_ticker.price_usd > 0:
        eth_btc_ratio = (eth_ticker.price_usd / btc_ticker.price_usd).quantize(
            Decimal("0.0000000001"), rounding=ROUND_HALF_UP
        )
        # ETH/BTC 7D change: compute from 7-day-ago prices if available
        if len(btc_candles) >= 8 and len(eth_candles) >= 8:
            btc_7d_ago = btc_candles[-8].close
            eth_7d_ago = eth_candles[-8].close
            if btc_7d_ago > 0 and eth_7d_ago > 0:
                ratio_7d_ago = eth_7d_ago / btc_7d_ago
                if ratio_7d_ago > 0:
                    eth_btc_change_7d = (
                        ((eth_btc_ratio - ratio_7d_ago) / ratio_7d_ago) * Decimal("100")
                    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # BTC volume dominance (volume-based proxy, NOT market-cap-based)
    btc_dominance = provider.compute_btc_volume_dominance(all_tickers)

    # Total USDT volume across all pairs
    total_usdt_volume = sum(
        (t.quote_volume_24h_usd or Decimal("0") for t in all_tickers),
        Decimal("0"),
    )

    # Classify regime
    regime, confidence = _classify_regime(
        btc_above_50dma=btc_above_50dma,
        btc_change_7d_pct=btc_change_7d,
        eth_btc_change_7d_pct=eth_btc_change_7d,
    )

    with transaction.atomic():
        snapshot = MarketRegimeSnapshot.objects.create(
            btc_price_usd=btc_ticker.price_usd,
            eth_price_usd=eth_ticker.price_usd,
            btc_change_7d_pct=btc_change_7d,
            eth_change_7d_pct=eth_change_7d,
            btc_above_50dma=btc_above_50dma,
            btc_50dma_value=btc_50dma,
            eth_btc_ratio=eth_btc_ratio,
            eth_btc_change_7d_pct=eth_btc_change_7d,
            btc_volume_dominance_pct=btc_dominance,
            total_usdt_volume_24h_usd=total_usdt_volume if total_usdt_volume > 0 else None,
            regime=regime,
            regime_confidence=confidence,
            source="binance",
            observed_at=now,
        )

    job.assets_attempted = 1
    job.assets_succeeded = 1
    job.status = DataIngestionJob.Status.SUCCESS
    job.finished_at = datetime.now(timezone.utc)
    job.save()

    return {
        "regime": regime,
        "btc_price": str(btc_ticker.price_usd),
        "eth_price": str(eth_ticker.price_usd),
        "confidence": str(confidence),
    }


def _compute_7d_return(candles) -> Decimal | None:
    """Compute 7-day return from daily candles. Returns percentage, or
    None if insufficient data."""
    if len(candles) < 8:
        return None
    current = candles[-1].close
    baseline = candles[-8].close
    if baseline <= 0:
        return None
    return (((current - baseline) / baseline) * Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _classify_regime(
    *,
    btc_above_50dma: bool | None,
    btc_change_7d_pct: Decimal | None,
    eth_btc_change_7d_pct: Decimal | None,
) -> tuple[str, Decimal]:
    """Classify market regime based on indicators. Returns (regime, confidence).

    Confidence is a rough 0-1 score based on how many indicators agree.
    This is deliberately simple — a placeholder for a more sophisticated
    regime model once backtesting exists to validate what actually works."""
    signals = []
    if btc_above_50dma is not None:
        signals.append("btc_trend_up" if btc_above_50dma else "btc_trend_down")
    if btc_change_7d_pct is not None:
        if btc_change_7d_pct > 5:
            signals.append("btc_momentum_up")
        elif btc_change_7d_pct < -5:
            signals.append("btc_momentum_down")
    if eth_btc_change_7d_pct is not None:
        if eth_btc_change_7d_pct > 1:
            signals.append("altcoin_strength")
        elif eth_btc_change_7d_pct < -1:
            signals.append("altcoin_weakness")

    if not signals:
        return MarketRegimeSnapshot.Regime.NEUTRAL, Decimal("0.3")

    bullish_count = sum(1 for s in signals if s in ("btc_trend_up", "btc_momentum_up", "altcoin_strength"))
    bearish_count = sum(1 for s in signals if s in ("btc_trend_down", "btc_momentum_down", "altcoin_weakness"))
    total = len(signals)

    if bullish_count > bearish_count:
        confidence = Decimal(str(bullish_count / total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return MarketRegimeSnapshot.Regime.BULLISH, confidence
    elif bearish_count > bullish_count:
        confidence = Decimal(str(bearish_count / total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return MarketRegimeSnapshot.Regime.BEARISH, confidence
    else:
        return MarketRegimeSnapshot.Regime.NEUTRAL, Decimal("0.5")
