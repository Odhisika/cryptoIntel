from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.models import Asset, MarketSnapshot, ScoreFactor, ScoreSnapshot
from core.scoring.persistence import save_score_result
from core.scoring.potential_10x import MODEL_VERSION, compute_10x_potential_score
from core.scoring.undervaluation import compute_undervaluation_score

pytestmark = pytest.mark.django_db


def make_asset_with_snapshot():
    asset = Asset.objects.create(symbol="tst", name="Test Coin", external_ids={"coingecko": "test-coin"})
    snapshot = MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
        fully_diluted_valuation_usd=Decimal("100000000"), volume_24h_usd=Decimal("5000000"),
        source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    return asset, snapshot


def test_undervaluation_score_is_zero_with_zero_confidence_pre_phase_3():
    asset, snapshot = make_asset_with_snapshot()
    result = compute_undervaluation_score(asset, snapshot)
    assert result.score == Decimal("0")
    assert result.data_confidence == Decimal("0")
    assert len(result.missing_factors()) == len(result.factors)


def test_save_score_result_creates_snapshot_and_factors():
    asset, snapshot = make_asset_with_snapshot()
    result = compute_10x_potential_score(asset, snapshot)

    saved = save_score_result(asset, snapshot, result)

    assert ScoreSnapshot.objects.count() == 1
    assert saved.model_name == "10x_potential"
    assert saved.model_version == MODEL_VERSION
    assert saved.score == result.score
    assert ScoreFactor.objects.filter(score_snapshot=saved).count() == len(result.factors)


def test_save_score_result_is_idempotent_on_rerun():
    asset, snapshot = make_asset_with_snapshot()
    result = compute_10x_potential_score(asset, snapshot)

    save_score_result(asset, snapshot, result)
    save_score_result(asset, snapshot, result)  # re-run, e.g. score recomputed after a bugfix

    assert ScoreSnapshot.objects.count() == 1
    saved = ScoreSnapshot.objects.get()
    assert ScoreFactor.objects.filter(score_snapshot=saved).count() == len(result.factors)


def test_different_model_versions_produce_separate_snapshots():
    asset, snapshot = make_asset_with_snapshot()
    result = compute_10x_potential_score(asset, snapshot)

    save_score_result(asset, snapshot, result)

    # Simulate a later model version existing — same asset/snapshot, different version.
    from core.scoring.base import compute_weighted_score, Factor
    other_version_result = compute_weighted_score(
        model_name="10x_potential",
        model_version=f"{MODEL_VERSION}-test-next",
        factors=[Factor(name="x", weight=Decimal("100"), normalized_value=Decimal("50"), raw_value="x")],
    )
    save_score_result(asset, snapshot, other_version_result)

    assert ScoreSnapshot.objects.filter(asset=asset, model_name="10x_potential").count() == 2
