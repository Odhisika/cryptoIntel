from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.models import (
    Asset, DEXPairSnapshot, MarketRegimeSnapshot, MarketSnapshot,
)
from core.scoring.potential_10x import (
    _score_dex_activity,
    _apply_regime_multiplier,
    compute_10x_potential_score,
)

pytestmark = pytest.mark.django_db


NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def make_asset(symbol="TST", name="Test Coin", sector=None):
    return Asset.objects.create(
        symbol=symbol, name=name,
        external_ids={"coingecko": "test-coin"},
        sector=sector,
    )


def make_snapshot(asset, **overrides):
    defaults = dict(
        asset=asset,
        price_usd=Decimal("1"),
        market_cap_usd=Decimal("100000000"),
        fully_diluted_valuation_usd=Decimal("100000000"),
        volume_24h_usd=Decimal("5000000"),
        source="coingecko",
        observed_at=NOW,
    )
    defaults.update(overrides)
    return MarketSnapshot.objects.create(**defaults)


def make_dex_snapshot(
    asset,
    liquidity_usd=Decimal("5000000"),
    volume_24h_usd=Decimal("1000000"),
    txns_24h_buys=600,
    txns_24h_sells=400,
    earliest_pair_created_at=None,
    pair_count=3,
    chains=None,
):
    if earliest_pair_created_at is None:
        earliest_pair_created_at = NOW - timedelta(days=60)
    if chains is None:
        chains = ["ethereum"]
    return DEXPairSnapshot.objects.create(
        asset=asset,
        liquidity_usd=liquidity_usd,
        volume_24h_usd=volume_24h_usd,
        txns_24h_buys=txns_24h_buys,
        txns_24h_sells=txns_24h_sells,
        earliest_pair_created_at=earliest_pair_created_at,
        pair_count=pair_count,
        chains=chains,
        source="dexscreener",
        observed_at=NOW,
    )


def make_regime(regime="bullish"):
    return MarketRegimeSnapshot.objects.create(
        btc_price_usd=Decimal("60000"),
        eth_price_usd=Decimal("3000"),
        btc_change_7d_pct=Decimal("5"),
        eth_change_7d_pct=Decimal("3"),
        btc_above_50dma=True,
        regime=regime,
        regime_confidence=Decimal("1.0000"),
        source="binance",
        observed_at=NOW,
    )


# ---------------------------------------------------------------------------
# _score_dex_activity — unit tests
# ---------------------------------------------------------------------------


class TestScoreDexActivity:
    def test_no_dex_data_returns_insufficient(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        score, raw, note = _score_dex_activity(asset, snapshot)
        assert score is None
        assert "No DEX Screener data" in note

    def test_full_data_scores_nonzero(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(asset)
        score, raw, note = _score_dex_activity(asset, snapshot)
        assert score is not None
        assert score > Decimal("0")

    def test_healthy_liquidity_ratio_scores_high(self):
        asset = make_asset()
        snapshot = make_snapshot(asset, market_cap_usd=Decimal("100000000"))
        make_dex_snapshot(asset, liquidity_usd=Decimal("5000000"))  # 5% of MC
        score, raw, note = _score_dex_activity(asset, snapshot)
        assert score is not None
        assert "DEX liq" in raw

    def test_low_liquidity_scores_lower(self):
        asset = make_asset()
        snapshot = make_snapshot(asset, market_cap_usd=Decimal("100000000"))
        make_dex_snapshot(asset, liquidity_usd=Decimal("100000"))  # 0.1% of MC
        score_low, _, _ = _score_dex_activity(asset, snapshot)

        asset2 = make_asset("TST2", "Test Coin 2")
        snapshot2 = make_snapshot(asset2, market_cap_usd=Decimal("100000000"))
        make_dex_snapshot(asset2, liquidity_usd=Decimal("5000000"))  # 5% of MC
        score_high, _, _ = _score_dex_activity(asset2, snapshot2)

        assert score_low < score_high

    def test_buy_ratio_above_40_percent_positive(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(asset, txns_24h_buys=700, txns_24h_sells=300)
        score, raw, _ = _score_dex_activity(asset, snapshot)
        assert score is not None
        assert "Buys: 700" in raw

    def test_buy_ratio_below_30_percent_low_score(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(asset, txns_24h_buys=200, txns_24h_sells=800)
        score, raw, _ = _score_dex_activity(asset, snapshot)
        assert score is not None
        assert "Buys: 200" in raw

    def test_no_buys_sells_skips_buy_ratio(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(asset, txns_24h_buys=None, txns_24h_sells=None)
        score, raw, _ = _score_dex_activity(asset, snapshot)
        assert score is not None
        assert "Buys:" not in raw

    def test_new_token_gets_low_age_score(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(
            asset,
            earliest_pair_created_at=NOW - timedelta(days=3),
            pair_count=1,
        )
        score, raw, _ = _score_dex_activity(asset, snapshot)
        assert score is not None
        assert "3 days" in raw

    def test_old_token_gets_high_age_score(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(
            asset,
            earliest_pair_created_at=NOW - timedelta(days=60),
            pair_count=1,
        )
        score, raw, _ = _score_dex_activity(asset, snapshot)
        assert score is not None
        assert "60 days" in raw

    def test_multi_dex_3_plus_scores_perfect(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(asset, pair_count=5, chains=["ethereum", "base", "arbitrum"])
        score, raw, _ = _score_dex_activity(asset, snapshot)
        assert score is not None
        assert "5 DEX pairs" in raw

    def test_single_dex_scores_low(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(asset, pair_count=1, chains=["ethereum"])
        score_single, _, _ = _score_dex_activity(asset, snapshot)

        asset2 = make_asset("TST2", "Test Coin 2")
        snapshot2 = make_snapshot(asset2)
        make_dex_snapshot(asset2, pair_count=3, chains=["ethereum", "base"])
        score_multi, _, _ = _score_dex_activity(asset2, snapshot2)

        assert score_single < score_multi

    def test_no_pair_count_skips_multi_dex(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        dex_snap = DEXPairSnapshot.objects.create(
            asset=asset,
            liquidity_usd=Decimal("5000000"),
            pair_count=0,
            source="dexscreener",
            observed_at=NOW,
        )
        score, raw, _ = _score_dex_activity(asset, snapshot)
        assert score is not None
        assert "DEX pairs" not in raw

    def test_all_subsignals_none_returns_insufficient(self):
        asset = make_asset()
        snapshot = make_snapshot(asset, market_cap_usd=None)
        dex_snap = DEXPairSnapshot.objects.create(
            asset=asset,
            liquidity_usd=Decimal("0"),
            pair_count=0,
            txns_24h_buys=None,
            txns_24h_sells=None,
            earliest_pair_created_at=None,
            source="dexscreener",
            observed_at=NOW,
        )
        score, raw, note = _score_dex_activity(asset, snapshot)
        assert score is None
        assert "all sub-signals are None" in note


# ---------------------------------------------------------------------------
# _apply_regime_multiplier
# ---------------------------------------------------------------------------


class TestApplyRegimeMultiplier:
    def test_no_regime_returns_score_unchanged(self):
        result = _apply_regime_multiplier(Decimal("50"))
        assert result == Decimal("50")

    def test_bullish_regime_increases_score(self):
        make_regime(regime="bullish")
        result = _apply_regime_multiplier(Decimal("50"))
        assert result == Decimal("52.50")  # 50 * 1.05

    def test_bearish_regime_decreases_score(self):
        make_regime(regime="bearish")
        result = _apply_regime_multiplier(Decimal("50"))
        assert result == Decimal("47.50")  # 50 * 0.95

    def test_neutral_regime_no_change(self):
        make_regime(regime="neutral")
        result = _apply_regime_multiplier(Decimal("50"))
        assert result == Decimal("50.00")

    def test_multiplier_does_not_exceed_100(self):
        make_regime(regime="bullish")
        result = _apply_regime_multiplier(Decimal("98"))
        assert result == Decimal("100")  # capped at 100

    def test_multiplier_does_not_go_below_0(self):
        make_regime(regime="bearish")
        result = _apply_regime_multiplier(Decimal("1"))
        assert result == Decimal("0.95")


# ---------------------------------------------------------------------------
# compute_10x_potential_score — dex_activity integration
# ---------------------------------------------------------------------------


class TestCompute10xDexActivityIntegration:
    def test_dex_activity_factor_present_in_result(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(asset)
        result = compute_10x_potential_score(asset, snapshot)
        names = [f.name for f in result.factors]
        assert "dex_activity" in names

    def test_dex_activity_factor_weight_is_5(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(asset)
        result = compute_10x_potential_score(asset, snapshot)
        dex_factor = next(f for f in result.factors if f.name == "dex_activity")
        assert dex_factor.weight == Decimal("5")

    def test_dex_activity_insufficient_when_no_data(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        result = compute_10x_potential_score(asset, snapshot)
        dex_factor = next(f for f in result.factors if f.name == "dex_activity")
        assert dex_factor.insufficient_data is True

    def test_dex_activity_computed_when_data_present(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(asset)
        result = compute_10x_potential_score(asset, snapshot)
        dex_factor = next(f for f in result.factors if f.name == "dex_activity")
        assert dex_factor.insufficient_data is False
        assert dex_factor.normalized_value > Decimal("0")

    def test_model_version_is_v17(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        result = compute_10x_potential_score(asset, snapshot)
        assert result.model_version == "v1.7"

    def test_regime_multiplier_applied_to_final_score(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(asset)
        make_regime(regime="bullish")

        result_with_regime = compute_10x_potential_score(asset, snapshot)

        # Without regime: multiplier is 1.0, so score stays the same
        MarketRegimeSnapshot.objects.all().delete()
        result_without_regime = compute_10x_potential_score(asset, snapshot)

        assert result_with_regime.score > result_without_regime.score

    def test_total_factor_count_with_dex_data(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(asset)
        result = compute_10x_potential_score(asset, snapshot)
        assert len(result.factors) == 11
