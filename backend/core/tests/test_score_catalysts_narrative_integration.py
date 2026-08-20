from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.models import Asset, Catalyst, MarketSnapshot, ScoreSnapshot
from core.scoring.potential_10x import compute_10x_potential_score

pytestmark = pytest.mark.django_db


def make_asset_with_snapshot(sector=None):
    asset = Asset.objects.create(symbol="tst", name="Test Coin", external_ids={"coingecko": "test"}, sector=sector)
    snapshot = MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
        source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    return asset, snapshot


# --- Catalysts ---

def test_catalysts_insufficient_without_any_curated_entry():
    asset, snapshot = make_asset_with_snapshot()
    result = compute_10x_potential_score(asset, snapshot)
    cat = next(f for f in result.factors if f.name == "catalysts")
    assert cat.insufficient_data is True
    assert "not evidence" in cat.note


def test_catalysts_scores_high_impact_confirmed_catalyst_highest():
    asset, snapshot = make_asset_with_snapshot()
    Catalyst.objects.create(
        asset=asset, title="Mainnet Launch", description="x",
        catalyst_type=Catalyst.CatalystType.MAINNET, event_date=date.today() + timedelta(days=10),
        source_url="https://example.com", confidence=Catalyst.Confidence.CONFIRMED,
        impact_estimate="high", status=Catalyst.Status.UPCOMING,
    )
    result = compute_10x_potential_score(asset, snapshot)
    cat = next(f for f in result.factors if f.name == "catalysts")
    assert cat.normalized_value == Decimal("100")  # high(100) * confirmed(1.0)


def test_catalysts_speculative_scores_lower_than_confirmed():
    asset, snapshot = make_asset_with_snapshot()
    Catalyst.objects.create(
        asset=asset, title="Rumored Partnership", description="x",
        catalyst_type=Catalyst.CatalystType.PARTNERSHIP, event_date=date.today() + timedelta(days=10),
        source_url="https://example.com", confidence=Catalyst.Confidence.SPECULATIVE,
        impact_estimate="high", status=Catalyst.Status.UPCOMING,
    )
    result = compute_10x_potential_score(asset, snapshot)
    cat = next(f for f in result.factors if f.name == "catalysts")
    assert cat.normalized_value == Decimal("30")  # high(100) * speculative(0.3)


def test_catalysts_past_events_are_ignored():
    asset, snapshot = make_asset_with_snapshot()
    Catalyst.objects.create(
        asset=asset, title="Old Event", description="x",
        catalyst_type=Catalyst.CatalystType.MAINNET, event_date=date.today() - timedelta(days=10),
        source_url="https://example.com", confidence=Catalyst.Confidence.CONFIRMED,
        impact_estimate="high", status=Catalyst.Status.COMPLETED,
    )
    result = compute_10x_potential_score(asset, snapshot)
    cat = next(f for f in result.factors if f.name == "catalysts")
    assert cat.insufficient_data is True


def test_cancelled_catalysts_are_ignored():
    asset, snapshot = make_asset_with_snapshot()
    Catalyst.objects.create(
        asset=asset, title="Cancelled Thing", description="x",
        catalyst_type=Catalyst.CatalystType.PARTNERSHIP, event_date=date.today() + timedelta(days=10),
        source_url="https://example.com", confidence=Catalyst.Confidence.LIKELY,
        impact_estimate="high", status=Catalyst.Status.CANCELLED,
    )
    result = compute_10x_potential_score(asset, snapshot)
    cat = next(f for f in result.factors if f.name == "catalysts")
    assert cat.insufficient_data is True


# --- Narrative/Sector ---

def test_narrative_insufficient_without_sector():
    asset, snapshot = make_asset_with_snapshot(sector=None)
    result = compute_10x_potential_score(asset, snapshot)
    narrative = next(f for f in result.factors if f.name == "narrative_sector")
    assert narrative.insufficient_data is True


def test_narrative_insufficient_below_min_sector_peers():
    asset, snapshot = make_asset_with_snapshot(sector=Asset.Sector.L1)
    result = compute_10x_potential_score(asset, snapshot)
    narrative = next(f for f in result.factors if f.name == "narrative_sector")
    assert narrative.insufficient_data is True


def test_narrative_computed_with_enough_sector_peers():
    asset, snapshot = make_asset_with_snapshot(sector=Asset.Sector.L1)
    for i, score in enumerate([Decimal("40"), Decimal("60"), Decimal("80")]):
        peer = Asset.objects.create(symbol=f"peer{i}", name=f"Peer{i}", sector=Asset.Sector.L1)
        peer_snap = MarketSnapshot.objects.create(
            asset=peer, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
            source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        ScoreSnapshot.objects.create(
            asset=peer, market_snapshot=peer_snap, model_name="momentum", model_version="v1.0",
            score=score, data_confidence=Decimal("1.0"),
        )

    result = compute_10x_potential_score(asset, snapshot)
    narrative = next(f for f in result.factors if f.name == "narrative_sector")
    assert narrative.insufficient_data is False
    assert narrative.normalized_value == Decimal("60")  # median of 40/60/80
