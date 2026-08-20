from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.models import Asset, DEXPairSnapshot, MarketSnapshot
from core.scoring.risk import _score_dex_risk, compute_risk_score

pytestmark = pytest.mark.django_db


NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def make_asset(symbol="TST", name="Test Coin"):
    return Asset.objects.create(symbol=symbol, name=name, external_ids={"coingecko": "test-coin"})


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
    txns_24h_buys=None,
    txns_24h_sells=None,
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


# ---------------------------------------------------------------------------
# _score_dex_risk — unit tests
# ---------------------------------------------------------------------------


class TestScoreDexRisk:
    def test_no_dex_data_returns_insufficient(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is None
        assert "No DEX Screener data" in note

    def test_healthy_dex_data_gives_moderate_risk(self):
        asset = make_asset()
        snapshot = make_snapshot(asset, market_cap_usd=Decimal("100000000"))
        make_dex_snapshot(
            asset,
            liquidity_usd=Decimal("10000000"),
            volume_24h_usd=Decimal("10000000"),
            pair_count=5,
            earliest_pair_created_at=NOW - timedelta(days=120),
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is not None
        assert score < Decimal("50")

    def test_very_low_liquidity_gives_high_risk(self):
        asset = make_asset()
        snapshot = make_snapshot(asset, market_cap_usd=Decimal("100000000"))
        make_dex_snapshot(
            asset,
            liquidity_usd=Decimal("500000"),  # 0.5% of MC
            volume_24h_usd=Decimal("5000000"),
            pair_count=5,
            earliest_pair_created_at=NOW - timedelta(days=60),
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is not None
        assert "Very low DEX liquidity" in raw

    def test_low_liquidity_moderate_risk(self):
        asset = make_asset()
        snapshot = make_snapshot(asset, market_cap_usd=Decimal("100000000"))
        make_dex_snapshot(
            asset,
            liquidity_usd=Decimal("2000000"),  # 2% of MC, between 1% and 3%
            volume_24h_usd=Decimal("5000000"),
            pair_count=5,
            earliest_pair_created_at=NOW - timedelta(days=60),
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is not None
        assert "Low DEX liquidity" in raw

    def test_healthy_liquidity_low_risk(self):
        asset = make_asset()
        snapshot = make_snapshot(asset, market_cap_usd=Decimal("100000000"))
        make_dex_snapshot(
            asset,
            liquidity_usd=Decimal("10000000"),  # 10% of MC, well above 3%
            volume_24h_usd=Decimal("5000000"),
            pair_count=5,
            earliest_pair_created_at=NOW - timedelta(days=60),
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is not None
        assert "DEX liquidity:" in raw

    def test_very_new_token_high_risk(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(
            asset,
            earliest_pair_created_at=NOW - timedelta(days=3),
            pair_count=1,
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is not None
        assert "Very new token: 3 days" in raw

    def test_new_token_moderate_risk(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(
            asset,
            earliest_pair_created_at=NOW - timedelta(days=15),
            pair_count=2,
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is not None
        assert "New token: 15 days" in raw

    def test_old_token_low_risk(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(
            asset,
            earliest_pair_created_at=NOW - timedelta(days=90),
            pair_count=3,
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is not None
        assert "Token age: 90 days" in raw

    def test_single_dex_high_concentration_risk(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(
            asset,
            pair_count=1,
            chains=["ethereum"],
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is not None
        assert "Single DEX pair" in raw

    def test_two_dex_moderate_concentration_risk(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(
            asset,
            pair_count=2,
            chains=["ethereum", "base"],
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is not None
        assert "2 DEX pairs" in raw

    def test_three_plus_dex_low_concentration_risk(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(
            asset,
            pair_count=5,
            chains=["ethereum", "base", "arbitrum"],
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is not None
        assert "5 DEX pairs" in raw

    def test_very_low_volume_high_risk(self):
        asset = make_asset()
        snapshot = make_snapshot(asset, market_cap_usd=Decimal("100000000"))
        make_dex_snapshot(
            asset,
            volume_24h_usd=Decimal("500000"),  # 0.5% of MC
            pair_count=3,
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is not None
        assert "Very low DEX volume" in raw

    def test_low_volume_moderate_risk(self):
        asset = make_asset()
        snapshot = make_snapshot(asset, market_cap_usd=Decimal("100000000"))
        make_dex_snapshot(
            asset,
            volume_24h_usd=Decimal("2000000"),  # 2% of MC
            pair_count=3,
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is not None
        assert "Low DEX volume" in raw

    def test_healthy_volume_low_risk(self):
        asset = make_asset()
        snapshot = make_snapshot(asset, market_cap_usd=Decimal("100000000"))
        make_dex_snapshot(
            asset,
            volume_24h_usd=Decimal("10000000"),  # 10% of MC
            pair_count=3,
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is not None
        assert "DEX volume:" in raw

    def test_no_volume_skips_volume_risk_signal(self):
        asset = make_asset()
        snapshot = make_snapshot(asset, market_cap_usd=Decimal("100000000"))
        make_dex_snapshot(
            asset,
            volume_24h_usd=None,
            pair_count=3,
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is not None
        assert "volume" not in raw.lower()

    def test_no_pair_count_skips_dex_concentration_signal(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        DEXPairSnapshot.objects.create(
            asset=asset,
            liquidity_usd=Decimal("5000000"),
            pair_count=0,
            source="dexscreener",
            observed_at=NOW,
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is not None
        assert "DEX pair" not in raw

    def test_no_pair_created_at_skips_age_signal(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        DEXPairSnapshot.objects.create(
            asset=asset,
            liquidity_usd=Decimal("5000000"),
            pair_count=3,
            earliest_pair_created_at=None,
            source="dexscreener",
            observed_at=NOW,
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is not None
        assert "days" not in raw

    def test_no_risk_signals_computed_returns_insufficient(self):
        asset = make_asset()
        snapshot = make_snapshot(asset, market_cap_usd=None)
        DEXPairSnapshot.objects.create(
            asset=asset,
            liquidity_usd=Decimal("0"),
            pair_count=0,
            earliest_pair_created_at=None,
            volume_24h_usd=None,
            source="dexscreener",
            observed_at=NOW,
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is None
        assert "no risk signals" in note

    def test_multiple_risk_signals_averaged(self):
        asset = make_asset()
        snapshot = make_snapshot(asset, market_cap_usd=Decimal("100000000"))
        make_dex_snapshot(
            asset,
            liquidity_usd=Decimal("500000"),  # 0.5% -> very low liquidity
            volume_24h_usd=Decimal("500000"),  # 0.5% -> very low volume
            earliest_pair_created_at=NOW - timedelta(days=3),  # very new
            pair_count=1,  # single DEX
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is not None
        # All 4 risk signals present, high scores each
        assert score > Decimal("70")

    def test_mixed_risk_signals_averaged_moderately(self):
        asset = make_asset()
        snapshot = make_snapshot(asset, market_cap_usd=Decimal("100000000"))
        make_dex_snapshot(
            asset,
            liquidity_usd=Decimal("10000000"),  # healthy liquidity
            volume_24h_usd=Decimal("500000"),  # very low volume
            earliest_pair_created_at=NOW - timedelta(days=90),  # old token
            pair_count=1,  # single DEX
        )
        score, raw, note = _score_dex_risk(asset, snapshot)
        assert score is not None
        # 2 low risk + 2 high risk signals, average should be moderate
        assert Decimal("20") < score < Decimal("80")


# ---------------------------------------------------------------------------
# compute_risk_score — dex_risk factor integration
# ---------------------------------------------------------------------------


class TestRiskScoreDexRiskIntegration:
    def test_dex_risk_factor_present_in_result(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        result = compute_risk_score(asset, snapshot)
        names = [f.name for f in result.factors]
        assert "dex_risk" in names

    def test_dex_risk_factor_weight_is_5(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        result = compute_risk_score(asset, snapshot)
        dex_factor = next(f for f in result.factors if f.name == "dex_risk")
        assert dex_factor.weight == Decimal("5")

    def test_dex_risk_insufficient_when_no_data(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        result = compute_risk_score(asset, snapshot)
        dex_factor = next(f for f in result.factors if f.name == "dex_risk")
        assert dex_factor.insufficient_data is True

    def test_dex_risk_computed_when_data_present(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        make_dex_snapshot(asset)
        result = compute_risk_score(asset, snapshot)
        dex_factor = next(f for f in result.factors if f.name == "dex_risk")
        assert dex_factor.insufficient_data is False
        assert dex_factor.normalized_value > Decimal("0")

    def test_model_version_is_v13(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        result = compute_risk_score(asset, snapshot)
        assert result.model_version == "v1.3"

    def test_project_age_risk_not_in_factors(self):
        asset = make_asset()
        snapshot = make_snapshot(asset)
        result = compute_risk_score(asset, snapshot)
        names = [f.name for f in result.factors]
        assert "project_age_risk" not in names
