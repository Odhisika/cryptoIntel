from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.models import Asset, MarketSnapshot
from core.scoring.base import Factor, compute_weighted_score
from core.scoring.persistence import save_score_result
from core.scoring.ranking import rank_by_model

pytestmark = pytest.mark.django_db


def make_asset_with_snapshot(symbol, observed_at):
    asset = Asset.objects.create(symbol=symbol, name=symbol.upper(), external_ids={"coingecko": symbol})
    snapshot = MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
        source="coingecko", observed_at=observed_at,
    )
    return asset, snapshot


def score_result(value: Decimal, confidence_weight: Decimal = Decimal("100")):
    return compute_weighted_score(
        model_name="10x_potential",
        model_version="v1.0",
        factors=[Factor(name="x", weight=confidence_weight, normalized_value=value, raw_value="x")],
    )


def test_ranks_assets_highest_score_first():
    a, snap_a = make_asset_with_snapshot("aaa", datetime(2026, 1, 1, tzinfo=timezone.utc))
    b, snap_b = make_asset_with_snapshot("bbb", datetime(2026, 1, 1, tzinfo=timezone.utc))

    save_score_result(a, snap_a, score_result(Decimal("40")))
    save_score_result(b, snap_b, score_result(Decimal("90")))

    ranked = rank_by_model("10x_potential", "v1.0")
    assert [r.symbol for r in ranked] == ["bbb", "aaa"]
    assert ranked[0].rank == 1


def test_uses_only_the_most_recent_score_per_asset():
    a, snap_early = make_asset_with_snapshot("aaa", datetime(2026, 1, 1, tzinfo=timezone.utc))
    snap_late = MarketSnapshot.objects.create(
        asset=a, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
        source="coingecko", observed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    save_score_result(a, snap_early, score_result(Decimal("10")))
    save_score_result(a, snap_late, score_result(Decimal("95")))

    ranked = rank_by_model("10x_potential", "v1.0")
    assert len(ranked) == 1
    assert ranked[0].score == Decimal("95.00")


def test_min_data_confidence_filters_low_confidence_scores():
    a, snap_a = make_asset_with_snapshot("aaa", datetime(2026, 1, 1, tzinfo=timezone.utc))
    save_score_result(a, snap_a, score_result(Decimal("90")))  # confidence 1.0

    ranked_strict = rank_by_model("10x_potential", "v1.0", min_data_confidence=Decimal("0.5"))
    assert len(ranked_strict) == 1

    ranked_impossible = rank_by_model("10x_potential", "v1.0", min_data_confidence=Decimal("1.5"))
    assert ranked_impossible == []


def test_limit_caps_results():
    for i in range(5):
        a, snap = make_asset_with_snapshot(f"coin{i}", datetime(2026, 1, 1, tzinfo=timezone.utc))
        save_score_result(a, snap, score_result(Decimal(str(i * 10))))

    ranked = rank_by_model("10x_potential", "v1.0", limit=2)
    assert len(ranked) == 2


def test_different_model_versions_are_not_mixed():
    a, snap_a = make_asset_with_snapshot("aaa", datetime(2026, 1, 1, tzinfo=timezone.utc))
    v1_result = score_result(Decimal("50"))
    v1_1_result = compute_weighted_score(
        model_name="10x_potential",
        model_version="v1.1",
        factors=[Factor(name="x", weight=Decimal("100"), normalized_value=Decimal("99"), raw_value="x")],
    )
    save_score_result(a, snap_a, v1_result)
    save_score_result(a, snap_a, v1_1_result)

    ranked_v1 = rank_by_model("10x_potential", "v1.0")
    assert ranked_v1[0].score == Decimal("50.00")


def test_no_scores_returns_empty_list():
    assert rank_by_model("10x_potential", "v1.0") == []
