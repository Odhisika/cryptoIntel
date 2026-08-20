from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.models import Asset, MarketRegimeSnapshot, MarketSnapshot
from core.scoring.momentum import (
    _score_vs_btc,
    _score_vs_eth,
    _score_vs_sector,
    compute_momentum_score,
)

pytestmark = pytest.mark.django_db


NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def make_asset(symbol="TST", name="Test Coin", sector=None):
    return Asset.objects.create(
        symbol=symbol, name=name,
        external_ids={"coingecko": "test-coin"},
        sector=sector,
    )


def snap(asset, price, days_ago, volume=Decimal("1000000")):
    return MarketSnapshot.objects.create(
        asset=asset,
        price_usd=price,
        market_cap_usd=Decimal("100000000"),
        volume_24h_usd=volume,
        source="coingecko",
        observed_at=NOW - timedelta(days=days_ago),
    )


def make_regime(
    btc_change_7d_pct=Decimal("5"),
    eth_change_7d_pct=Decimal("3"),
    btc_price=Decimal("60000"),
    eth_price=Decimal("3000"),
):
    return MarketRegimeSnapshot.objects.create(
        btc_price_usd=btc_price,
        eth_price_usd=eth_price,
        btc_change_7d_pct=btc_change_7d_pct,
        eth_change_7d_pct=eth_change_7d_pct,
        btc_above_50dma=True,
        btc_50dma_value=Decimal("55000"),
        eth_btc_ratio=Decimal("0.0500000000"),
        eth_btc_change_7d_pct=Decimal("-1.67"),
        btc_volume_dominance_pct=Decimal("71.43"),
        total_usdt_volume_24h_usd=Decimal("100000000000"),
        regime=MarketRegimeSnapshot.Regime.BULLISH,
        regime_confidence=Decimal("1.0000"),
        source="binance",
        observed_at=NOW,
    )


# ---------------------------------------------------------------------------
# _score_vs_btc
# ---------------------------------------------------------------------------


class TestScoreVsBtc:
    def test_equal_returns_gives_neutral_score(self):
        score = _score_vs_btc(Decimal("10"), Decimal("10"))
        assert score == Decimal("50")

    def test_token_outperforms_btc_gives_above_50(self):
        score = _score_vs_btc(Decimal("20"), Decimal("5"))
        assert score > Decimal("50")

    def test_token_underperforms_btc_gives_below_50(self):
        score = _score_vs_btc(Decimal("5"), Decimal("20"))
        assert score < Decimal("50")

    def test_max_outperformance_caps_at_100(self):
        score = _score_vs_btc(Decimal("50"), Decimal("5"))
        assert score == Decimal("100")

    def test_max_underperformance_caps_at_0(self):
        score = _score_vs_btc(Decimal("-10"), Decimal("10"))
        assert score == Decimal("0")

    def test_moderate_outperformance(self):
        score = _score_vs_btc(Decimal("15"), Decimal("5"))
        # diff = 10, normalized = 10/20 * 50 + 50 = 75
        assert score == Decimal("75")


# ---------------------------------------------------------------------------
# _score_vs_eth
# ---------------------------------------------------------------------------


class TestScoreVsEth:
    def test_equal_returns_gives_neutral_score(self):
        score = _score_vs_eth(Decimal("10"), Decimal("10"))
        assert score == Decimal("50")

    def test_token_outperforms_eth_gives_above_50(self):
        score = _score_vs_eth(Decimal("20"), Decimal("5"))
        assert score > Decimal("50")

    def test_token_underperforms_eth_gives_below_50(self):
        score = _score_vs_eth(Decimal("0"), Decimal("10"))
        assert score < Decimal("50")


# ---------------------------------------------------------------------------
# _score_vs_sector
# ---------------------------------------------------------------------------


class TestScoreVsSector:
    def test_equal_to_sector_median_gives_neutral(self):
        score = _score_vs_sector(Decimal("10"), Decimal("10"))
        assert score == Decimal("50")

    def test_outperforming_sector_gives_above_50(self):
        score = _score_vs_sector(Decimal("15"), Decimal("10"))
        assert score > Decimal("50")

    def test_underperforming_sector_gives_below_50(self):
        score = _score_vs_sector(Decimal("5"), Decimal("10"))
        assert score < Decimal("50")


# ---------------------------------------------------------------------------
# compute_momentum_score — relative_strength integration
# ---------------------------------------------------------------------------


class TestMomentumRelativeStrength:
    def test_no_regime_snapshot_marks_relative_strength_insufficient(self):
        asset = make_asset()
        snap(asset, Decimal("1"), days_ago=7)
        current = snap(asset, Decimal("1.5"), days_ago=0)

        result = compute_momentum_score(asset, current)
        rel = next(f for f in result.factors if f.name == "relative_strength")
        assert rel.insufficient_data is True
        assert "No MarketRegimeSnapshot" in rel.note

    def test_no_7d_baseline_marks_relative_strength_insufficient(self):
        make_regime()
        asset = make_asset()
        current = snap(asset, Decimal("1"), days_ago=0)

        result = compute_momentum_score(asset, current)
        rel = next(f for f in result.factors if f.name == "relative_strength")
        assert rel.insufficient_data is True
        assert "No ~7D baseline" in rel.note

    def test_with_regime_and_baseline_produces_non_insufficient_strength(self):
        make_regime(btc_change_7d_pct=Decimal("5"), eth_change_7d_pct=Decimal("3"))
        asset = make_asset()
        snap(asset, Decimal("1"), days_ago=7)
        current = snap(asset, Decimal("1.5"), days_ago=0)

        result = compute_momentum_score(asset, current)
        rel = next(f for f in result.factors if f.name == "relative_strength")
        # Token outperformed BTC (50% vs 5%) and ETH (50% vs 3%), so score should be > 50
        assert rel.insufficient_data is False
        assert rel.normalized_value > Decimal("50")

    def test_token_underperforming_btc_and_eth_gives_low_relative_strength(self):
        make_regime(btc_change_7d_pct=Decimal("10"), eth_change_7d_pct=Decimal("8"))
        asset = make_asset()
        snap(asset, Decimal("1"), days_ago=7)
        current = snap(asset, Decimal("1.05"), days_ago=0)  # +5%, underperforms both

        result = compute_momentum_score(asset, current)
        rel = next(f for f in result.factors if f.name == "relative_strength")
        assert rel.insufficient_data is False
        assert rel.normalized_value < Decimal("50")

    def test_regime_with_no_btc_change_marks_btc_subsignal_none(self):
        make_regime(btc_change_7d_pct=None, eth_change_7d_pct=Decimal("3"))
        asset = make_asset()
        snap(asset, Decimal("1"), days_ago=7)
        current = snap(asset, Decimal("1.5"), days_ago=0)

        result = compute_momentum_score(asset, current)
        rel = next(f for f in result.factors if f.name == "relative_strength")
        assert rel.insufficient_data is False
        assert "vs BTC" not in (rel.raw_value or "")

    def test_regime_with_no_eth_change_marks_eth_subsignal_none(self):
        make_regime(btc_change_7d_pct=Decimal("5"), eth_change_7d_pct=None)
        asset = make_asset()
        snap(asset, Decimal("1"), days_ago=7)
        current = snap(asset, Decimal("1.5"), days_ago=0)

        result = compute_momentum_score(asset, current)
        rel = next(f for f in result.factors if f.name == "relative_strength")
        assert rel.insufficient_data is False
        assert "vs ETH" not in (rel.raw_value or "")

    def test_no_regime_data_does_not_cause_error(self):
        """Missing regime data should result in insufficient_data, not an exception."""
        asset = make_asset()
        snap(asset, Decimal("1"), days_ago=7)
        current = snap(asset, Decimal("1.5"), days_ago=0)

        result = compute_momentum_score(asset, current)
        assert result.score >= Decimal("0")
        rel = next(f for f in result.factors if f.name == "relative_strength")
        assert rel.insufficient_data is True

    def test_token_7d_return_none_marks_insufficient(self):
        """If token has no 7D baseline, relative strength is insufficient
        even if regime data is available."""
        make_regime()
        asset = make_asset()
        current = snap(asset, Decimal("1"), days_ago=0)

        result = compute_momentum_score(asset, current)
        rel = next(f for f in result.factors if f.name == "relative_strength")
        assert rel.insufficient_data is True

    def test_multiple_regime_snapshots_uses_latest(self):
        old_regime = MarketRegimeSnapshot.objects.create(
            btc_price_usd=Decimal("50000"),
            eth_price_usd=Decimal("2500"),
            btc_change_7d_pct=Decimal("-5"),
            eth_change_7d_pct=Decimal("-3"),
            btc_above_50dma=False,
            regime=MarketRegimeSnapshot.Regime.BEARISH,
            regime_confidence=Decimal("0.7500"),
            source="binance",
            observed_at=NOW - timedelta(days=1),
        )
        new_regime = MarketRegimeSnapshot.objects.create(
            btc_price_usd=Decimal("60000"),
            eth_price_usd=Decimal("3000"),
            btc_change_7d_pct=Decimal("10"),
            eth_change_7d_pct=Decimal("8"),
            btc_above_50dma=True,
            regime=MarketRegimeSnapshot.Regime.BULLISH,
            regime_confidence=Decimal("1.0000"),
            source="binance",
            observed_at=NOW,
        )

        asset = make_asset()
        snap(asset, Decimal("1"), days_ago=7)
        current = snap(asset, Decimal("2"), days_ago=0)

        result = compute_momentum_score(asset, current)
        rel = next(f for f in result.factors if f.name == "relative_strength")
        assert rel.insufficient_data is False
        assert "BTC 7D return: 10.0%" in rel.note

    def test_score_result_includes_relative_strength_factor(self):
        make_regime()
        asset = make_asset()
        snap(asset, Decimal("1"), days_ago=7)
        current = snap(asset, Decimal("1.5"), days_ago=0)

        result = compute_momentum_score(asset, current)
        factor_names = [f.name for f in result.factors]
        assert "relative_strength" in factor_names
        assert result.model_version == "v1.1"
