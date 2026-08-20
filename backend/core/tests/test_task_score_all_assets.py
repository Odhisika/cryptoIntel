from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from core.models import Asset, MarketSnapshot, ScoreSnapshot
from core.tasks.scoring import score_all_assets

pytestmark = pytest.mark.django_db


def make_asset_with_snapshot(symbol="tst"):
    asset = Asset.objects.create(symbol=symbol, name=symbol.upper(), external_ids={"coingecko": symbol})
    snapshot = MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
        fully_diluted_valuation_usd=Decimal("100000000"), volume_24h_usd=Decimal("5000000"),
        source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    return asset, snapshot


def test_scores_all_four_models_for_an_asset_with_a_snapshot():
    make_asset_with_snapshot()

    result = score_all_assets()

    assert result["scored"] == 1
    assert result["skipped_no_snapshot"] == 0
    assert ScoreSnapshot.objects.count() == 4
    model_names = set(ScoreSnapshot.objects.values_list("model_name", flat=True))
    assert model_names == {"10x_potential", "undervaluation", "momentum", "risk"}


def test_asset_without_snapshot_is_skipped_not_errored():
    Asset.objects.create(symbol="nosnap", name="No Snapshot", external_ids={"coingecko": "nosnap"})

    result = score_all_assets()

    assert result["scored"] == 0
    assert result["skipped_no_snapshot"] == 1
    assert ScoreSnapshot.objects.count() == 0


def test_inactive_assets_are_not_scored():
    asset, _ = make_asset_with_snapshot()
    asset.is_active = False
    asset.save()

    result = score_all_assets()

    assert result["scored"] == 0
    assert ScoreSnapshot.objects.count() == 0


def test_one_scorer_failing_does_not_block_the_others_or_other_assets():
    make_asset_with_snapshot("aaa")
    make_asset_with_snapshot("bbb")

    with patch(
        "core.scoring.momentum.compute_momentum_score", side_effect=RuntimeError("boom")
    ):
        result = score_all_assets()

    # Both assets still get "scored" (the other 3 scorers succeeded for each).
    assert result["scored"] == 2
    assert result["scorer_errors"] == 2
    # 2 assets * 3 successful scorers each = 6 ScoreSnapshots, momentum never written.
    assert ScoreSnapshot.objects.count() == 6
    assert not ScoreSnapshot.objects.filter(model_name="momentum").exists()


def test_narrative_sector_sees_all_batch_peers_regardless_of_iteration_order():
    """Regression test for the ordering bug caught during final review
    (see PHASE_9_NOTES.md / core/tasks/scoring.py's OTHER_SCORERS
    comment): before the momentum-first-pass fix, an asset processed
    early in Asset.objects' default ordering would compute its
    narrative_sector factor BEFORE its same-batch sector peers had any
    momentum data yet, incorrectly landing on insufficient_data even
    though the peers' data existed by the time the whole batch finished.

    This test deliberately creates the candidate asset FIRST (so it's
    earliest in insertion-order iteration) and its 3 sector peers AFTER
    it, then asserts the candidate's narrative_sector factor is NOT
    insufficient_data post-batch — which only holds if momentum for the
    peers is guaranteed to exist before 10X Potential Score runs for
    anyone, regardless of processing order."""

    candidate = Asset.objects.create(
        symbol="candidate", name="Candidate", sector=Asset.Sector.L1, external_ids={"coingecko": "candidate"}
    )
    MarketSnapshot.objects.create(
        asset=candidate, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
        source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc) - timedelta(days=7),
    )
    MarketSnapshot.objects.create(
        asset=candidate, price_usd=Decimal("1.1"), market_cap_usd=Decimal("110000000"),
        source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    # Peers created AFTER the candidate, so they sort later in default
    # (insertion/PK) iteration order — exactly the ordering that exposed
    # the bug. Each needs real (non-zero-confidence) momentum data too —
    # two snapshots 7 days apart — so this test isolates the ORDERING fix
    # specifically, not the separate zero-confidence-filtering fix in
    # core.scoring.narrative (see test_narrative.py for that one).
    for i in range(3):
        peer = Asset.objects.create(symbol=f"peer{i}", name=f"Peer{i}", sector=Asset.Sector.L1)
        MarketSnapshot.objects.create(
            asset=peer, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
            source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc) - timedelta(days=7),
        )
        MarketSnapshot.objects.create(
            asset=peer, price_usd=Decimal("1.2"), market_cap_usd=Decimal("120000000"),
            source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    score_all_assets()

    candidate_snapshot = ScoreSnapshot.objects.get(asset=candidate, model_name="10x_potential")
    narrative_factor = candidate_snapshot.score_factors.get(name="narrative_sector")
    assert narrative_factor.insufficient_data is False
