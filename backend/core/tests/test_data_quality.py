from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.models import Asset, DataIngestionJob, DataQualityIssue, MarketSnapshot
from core.providers.base import MarketSnapshotData
from core.tasks.ingestion import _flag_anomalies_if_any, find_stale_assets

pytestmark = pytest.mark.django_db


def make_asset(**kwargs):
    defaults = dict(symbol="btc", name="Bitcoin", external_ids={"coingecko": "bitcoin"})
    defaults.update(kwargs)
    return Asset.objects.create(**defaults)


def make_job():
    return DataIngestionJob.objects.create(provider="coingecko", job_type="market_snapshot")


def snap(**kwargs):
    defaults = dict(
        external_id="bitcoin",
        symbol="btc",
        name="Bitcoin",
        price_usd=Decimal("100"),
        market_cap_usd=Decimal("1000000"),
        fully_diluted_valuation_usd=None,
        volume_24h_usd=None,
        circulating_supply=Decimal("1000000"),
        total_supply=Decimal("1000000"),
        max_supply=None,
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source="coingecko",
    )
    defaults.update(kwargs)
    return MarketSnapshotData(**defaults)


def test_impossible_price_is_flagged_critical():
    asset = make_asset()
    job = make_job()

    _flag_anomalies_if_any(asset, snap(price_usd=Decimal("0")), job)

    issue = DataQualityIssue.objects.get()
    assert issue.issue_type == "impossible_price"
    assert issue.severity == DataQualityIssue.Severity.CRITICAL


def test_large_price_jump_is_flagged_warning():
    asset = make_asset()
    job = make_job()
    MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("100"), source="coingecko",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    _flag_anomalies_if_any(
        asset,
        snap(price_usd=Decimal("5000"), observed_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc)),
        job,
    )

    issue_types = set(DataQualityIssue.objects.values_list("issue_type", flat=True))
    assert "price_anomaly" in issue_types


def test_normal_price_move_is_not_flagged():
    asset = make_asset()
    job = make_job()
    MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("100"), source="coingecko",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    _flag_anomalies_if_any(
        asset,
        snap(price_usd=Decimal("105"), observed_at=datetime(2026, 1, 1, 0, 15, tzinfo=timezone.utc)),
        job,
    )

    assert DataQualityIssue.objects.count() == 0


def test_stale_gap_between_snapshots_is_flagged():
    asset = make_asset()
    job = make_job()
    MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("100"), source="coingecko",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    _flag_anomalies_if_any(
        asset,
        snap(price_usd=Decimal("101"), observed_at=datetime(2026, 1, 2, tzinfo=timezone.utc)),
        job,
    )

    issue_types = set(DataQualityIssue.objects.values_list("issue_type", flat=True))
    assert "stale_data_gap" in issue_types


def test_sudden_supply_change_is_flagged_critical():
    asset = make_asset()
    job = make_job()
    MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("100"), source="coingecko",
        circulating_supply=Decimal("1000000"),
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    _flag_anomalies_if_any(
        asset,
        snap(
            price_usd=Decimal("101"),
            circulating_supply=Decimal("3000000"),
            observed_at=datetime(2026, 1, 1, 0, 15, tzinfo=timezone.utc),
        ),
        job,
    )

    issue = DataQualityIssue.objects.get(issue_type="sudden_supply_change")
    assert issue.severity == DataQualityIssue.Severity.CRITICAL


def test_find_stale_assets_includes_assets_with_no_snapshot():
    make_asset()
    stale = find_stale_assets()
    assert len(stale) == 1


def test_find_stale_assets_excludes_fresh_assets():
    asset = make_asset()
    MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("100"), source="coingecko",
        observed_at=datetime.now(timezone.utc),
    )
    stale = find_stale_assets()
    assert stale == []


def test_find_stale_assets_includes_assets_with_old_snapshot():
    asset = make_asset()
    MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("100"), source="coingecko",
        observed_at=datetime.now(timezone.utc) - timedelta(hours=48),
    )
    stale = find_stale_assets()
    assert stale == [asset]
