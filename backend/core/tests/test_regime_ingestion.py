from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest

from core.models import DataIngestionJob, MarketRegimeSnapshot
from core.providers.base import CandleData, TickerData, ProviderError
from core.tasks.regime_ingestion import _classify_regime, _compute_7d_return, ingest_market_regime

pytestmark = pytest.mark.django_db


NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def make_candle(close_price, days_offset=0):
    dt = NOW - timedelta(days=days_offset)
    return CandleData(
        symbol="BTCUSDT",
        open=close_price,
        high=close_price,
        low=close_price,
        close=close_price,
        volume=Decimal("1000"),
        close_time=dt,
    )


def make_candles(*close_prices):
    """Build candles from oldest to newest, spaced 1 day apart."""
    candles = []
    for i, price in enumerate(close_prices):
        candles.append(make_candle(price, days_offset=len(close_prices) - 1 - i))
    return candles


# ---------------------------------------------------------------------------
# _compute_7d_return
# ---------------------------------------------------------------------------


class TestCompute7dReturn:
    def test_basic_7d_return(self):
        candles = make_candles(
            Decimal("100"), Decimal("101"), Decimal("102"), Decimal("103"),
            Decimal("104"), Decimal("105"), Decimal("106"), Decimal("110"),
        )
        result = _compute_7d_return(candles)
        # (110 - 100) / 100 * 100 = 10%
        assert result == Decimal("10.00")

    def test_negative_7d_return(self):
        candles = make_candles(
            Decimal("100"), Decimal("99"), Decimal("98"), Decimal("97"),
            Decimal("96"), Decimal("95"), Decimal("94"), Decimal("90"),
        )
        result = _compute_7d_return(candles)
        assert result == Decimal("-10.00")

    def test_zero_baseline_returns_none(self):
        candles = make_candles(
            Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
            Decimal("0"), Decimal("0"), Decimal("0"), Decimal("100"),
        )
        result = _compute_7d_return(candles)
        assert result is None

    def test_insufficient_candles_returns_none(self):
        candles = make_candles(Decimal("100"), Decimal("101"), Decimal("102"))
        result = _compute_7d_return(candles)
        assert result is None

    def test_exactly_8_candles_is_sufficient(self):
        candles = make_candles(
            Decimal("50"), Decimal("51"), Decimal("52"), Decimal("53"),
            Decimal("54"), Decimal("55"), Decimal("56"), Decimal("60"),
        )
        result = _compute_7d_return(candles)
        assert result is not None
        assert result == Decimal("20.00")

    def test_empty_candles_returns_none(self):
        result = _compute_7d_return([])
        assert result is None


# ---------------------------------------------------------------------------
# _classify_regime
# ---------------------------------------------------------------------------


class TestClassifyRegime:
    def test_no_signals_returns_neutral_low_confidence(self):
        regime, confidence = _classify_regime(
            btc_above_50dma=None,
            btc_change_7d_pct=None,
            eth_btc_change_7d_pct=None,
        )
        assert regime == MarketRegimeSnapshot.Regime.NEUTRAL
        assert confidence == Decimal("0.3")

    def test_all_bullish_signals_returns_bullish(self):
        regime, confidence = _classify_regime(
            btc_above_50dma=True,
            btc_change_7d_pct=Decimal("10"),
            eth_btc_change_7d_pct=Decimal("3"),
        )
        assert regime == MarketRegimeSnapshot.Regime.BULLISH
        assert confidence > Decimal("0.5")

    def test_all_bearish_signals_returns_bearish(self):
        regime, confidence = _classify_regime(
            btc_above_50dma=False,
            btc_change_7d_pct=Decimal("-10"),
            eth_btc_change_7d_pct=Decimal("-3"),
        )
        assert regime == MarketRegimeSnapshot.Regime.BEARISH
        assert confidence > Decimal("0.5")

    def test_mixed_signals_returns_neutral(self):
        regime, confidence = _classify_regime(
            btc_above_50dma=True,
            btc_change_7d_pct=Decimal("-10"),
            eth_btc_change_7d_pct=None,
        )
        assert regime == MarketRegimeSnapshot.Regime.NEUTRAL
        assert confidence == Decimal("0.50")

    def test_bullish_trend_only(self):
        regime, confidence = _classify_regime(
            btc_above_50dma=True,
            btc_change_7d_pct=None,
            eth_btc_change_7d_pct=None,
        )
        assert regime == MarketRegimeSnapshot.Regime.BULLISH
        assert confidence == Decimal("1.00")

    def test_bearish_trend_only(self):
        regime, confidence = _classify_regime(
            btc_above_50dma=False,
            btc_change_7d_pct=None,
            eth_btc_change_7d_pct=None,
        )
        assert regime == MarketRegimeSnapshot.Regime.BEARISH
        assert confidence == Decimal("1.00")

    def test_btc_momentum_up_plus_altcoin_strength_is_bullish(self):
        regime, confidence = _classify_regime(
            btc_above_50dma=None,
            btc_change_7d_pct=Decimal("8"),
            eth_btc_change_7d_pct=Decimal("2"),
        )
        assert regime == MarketRegimeSnapshot.Regime.BULLISH

    def test_btc_momentum_down_plus_altcoin_weakness_is_bearish(self):
        regime, confidence = _classify_regime(
            btc_above_50dma=None,
            btc_change_7d_pct=Decimal("-8"),
            eth_btc_change_7d_pct=Decimal("-2"),
        )
        assert regime == MarketRegimeSnapshot.Regime.BEARISH

    def test_btc_momentum_threshold_boundary_at_5(self):
        regime_up, _ = _classify_regime(
            btc_above_50dma=None,
            btc_change_7d_pct=Decimal("6"),
            eth_btc_change_7d_pct=None,
        )
        regime_down, _ = _classify_regime(
            btc_above_50dma=None,
            btc_change_7d_pct=Decimal("-6"),
            eth_btc_change_7d_pct=None,
        )
        regime_neutral, _ = _classify_regime(
            btc_above_50dma=None,
            btc_change_7d_pct=Decimal("5"),
            eth_btc_change_7d_pct=None,
        )
        assert regime_up == MarketRegimeSnapshot.Regime.BULLISH
        assert regime_down == MarketRegimeSnapshot.Regime.BEARISH
        assert regime_neutral == MarketRegimeSnapshot.Regime.NEUTRAL

    def test_eth_btc_threshold_boundary_at_1(self):
        regime_up, _ = _classify_regime(
            btc_above_50dma=None,
            btc_change_7d_pct=None,
            eth_btc_change_7d_pct=Decimal("2"),
        )
        regime_down, _ = _classify_regime(
            btc_above_50dma=None,
            btc_change_7d_pct=None,
            eth_btc_change_7d_pct=Decimal("-2"),
        )
        assert regime_up == MarketRegimeSnapshot.Regime.BULLISH
        assert regime_down == MarketRegimeSnapshot.Regime.BEARISH


# ---------------------------------------------------------------------------
# ingest_market_regime — full task tests with mocked BinanceProvider
# ---------------------------------------------------------------------------


def _mock_binance_provider(
    btc_price=Decimal("60000"),
    eth_price=Decimal("3000"),
    btc_candles=None,
    eth_candles=None,
    all_tickers=None,
):
    provider = MagicMock()

    provider.fetch_24h_ticker.side_effect = lambda symbol: TickerData(
        symbol=symbol,
        price_usd=btc_price if symbol == "BTCUSDT" else eth_price,
        volume_24h_usd=Decimal("500000000"),
        price_change_pct=Decimal("2.5"),
        quote_volume_24h_usd=Decimal("500000000"),
        trades_count=1000000,
        observed_at=NOW,
    )

    if btc_candles is None:
        btc_candles = make_candles(
            *[Decimal("55000")] * 50 + [Decimal("60000")] * 20 + [Decimal("60000")] * 30
        )
    if eth_candles is None:
        eth_candles = make_candles(
            *[Decimal("2800")] * 50 + [Decimal("3000")] * 20 + [Decimal("3000")] * 30
        )

    provider.fetch_candles.side_effect = lambda symbol, interval, limit: btc_candles if symbol == "BTCUSDT" else eth_candles

    if all_tickers is None:
        all_tickers = [
            TickerData("BTCUSDT", btc_price, Decimal("500000000"), None, Decimal("500000000"), 1000000, NOW),
            TickerData("ETHUSDT", eth_price, Decimal("200000000"), None, Decimal("200000000"), 500000, NOW),
        ]
    provider.fetch_all_usdt_tickers.return_value = all_tickers
    provider.compute_daily_moving_average.return_value = Decimal("58000")
    provider.compute_btc_volume_dominance.return_value = Decimal("71.43")

    return provider


class TestIngestMarketRegime:
    @patch("core.tasks.regime_ingestion.BinanceProvider")
    def test_creates_regime_snapshot(self, MockProvider):
        MockProvider.return_value = _mock_binance_provider()

        result = ingest_market_regime()

        assert result["regime"] is not None
        assert MarketRegimeSnapshot.objects.count() == 1
        snap = MarketRegimeSnapshot.objects.get()
        assert snap.btc_price_usd == Decimal("60000")
        assert snap.eth_price_usd == Decimal("3000")
        assert snap.source == "binance"

    @patch("core.tasks.regime_ingestion.BinanceProvider")
    def test_job_marked_success(self, MockProvider):
        MockProvider.return_value = _mock_binance_provider()
        ingest_market_regime()

        job = DataIngestionJob.objects.get()
        assert job.status == DataIngestionJob.Status.SUCCESS

    @patch("core.tasks.regime_ingestion.BinanceProvider")
    def test_provider_failure_marks_job_failed(self, MockProvider):
        MockProvider.return_value = _mock_binance_provider()
        MockProvider.return_value.fetch_24h_ticker.side_effect = ProviderError(
            "binance", "rate limited", retryable=False
        )

        result = ingest_market_regime()
        assert result["succeeded"] == 0
        assert "error" in result
        job = DataIngestionJob.objects.get()
        assert job.status == DataIngestionJob.Status.FAILED

    @patch("core.tasks.regime_ingestion.BinanceProvider")
    def test_bullish_regime_when_btc_above_50dma_and_positive_momentum(self, MockProvider):
        # BTC at 60k, 50DMA at 55k -> above 50DMA
        # Candles that show +10% 7D return
        btc_candles = make_candles(*[Decimal("50000")] * 93 + [Decimal("60000")] * 7)
        eth_candles = make_candles(*[Decimal("2500")] * 93 + [Decimal("3000")] * 7)
        MockProvider.return_value = _mock_binance_provider(
            btc_candles=btc_candles,
            eth_candles=eth_candles,
        )

        ingest_market_regime()
        snap = MarketRegimeSnapshot.objects.get()
        assert snap.regime == MarketRegimeSnapshot.Regime.BULLISH

    @patch("core.tasks.regime_ingestion.BinanceProvider")
    def test_btc_above_50dma_flag_set_correctly(self, MockProvider):
        MockProvider.return_value = _mock_binance_provider(btc_price=Decimal("60000"))
        MockProvider.return_value.compute_daily_moving_average.return_value = Decimal("55000")

        ingest_market_regime()
        snap = MarketRegimeSnapshot.objects.get()
        assert snap.btc_above_50dma is True

    @patch("core.tasks.regime_ingestion.BinanceProvider")
    def test_btc_below_50dma_flag(self, MockProvider):
        MockProvider.return_value = _mock_binance_provider(btc_price=Decimal("50000"))
        MockProvider.return_value.compute_daily_moving_average.return_value = Decimal("55000")

        ingest_market_regime()
        snap = MarketRegimeSnapshot.objects.get()
        assert snap.btc_above_50dma is False

    @patch("core.tasks.regime_ingestion.BinanceProvider")
    def test_eth_btc_ratio_computed(self, MockProvider):
        MockProvider.return_value = _mock_binance_provider(
            btc_price=Decimal("60000"),
            eth_price=Decimal("3000"),
        )
        ingest_market_regime()
        snap = MarketRegimeSnapshot.objects.get()
        # 3000 / 60000 = 0.05
        assert snap.eth_btc_ratio is not None
        assert snap.eth_btc_ratio > 0
