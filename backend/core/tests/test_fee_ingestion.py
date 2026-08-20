import pytest
import responses

from core.models import DataIngestionJob, FeeSnapshot, Protocol, RevenueSnapshot
from core.providers.defillama import DEFILLAMA_BASE_URL
from core.tasks.fee_ingestion import ingest_fee_revenue_snapshots

pytestmark = pytest.mark.django_db

WITH_REVENUE = {
    "name": "Uniswap", "total24h": 2000000, "total7d": 14000000, "total30d": 60000000,
    "dailyRevenue": 500000,
}
NO_REVENUE = {
    "name": "SomeDex", "total24h": 100000, "total7d": 700000, "total30d": 3000000,
    "dailyRevenue": None,
}


def make_protocol(slug="uniswap"):
    return Protocol.objects.create(slug=slug, name=slug.title())


@responses.activate
def test_ingests_fee_and_revenue_snapshot_when_revenue_present():
    make_protocol("uniswap")
    responses.add(responses.GET, f"{DEFILLAMA_BASE_URL}/summary/fees/uniswap", json=WITH_REVENUE, status=200)

    result = ingest_fee_revenue_snapshots()

    assert result["succeeded"] == 1
    assert result["revenue_recorded"] == 1
    assert FeeSnapshot.objects.count() == 1
    assert RevenueSnapshot.objects.count() == 1


@responses.activate
def test_no_revenue_snapshot_created_when_protocol_takes_no_cut():
    make_protocol("some-dex")
    responses.add(responses.GET, f"{DEFILLAMA_BASE_URL}/summary/fees/some-dex", json=NO_REVENUE, status=200)

    result = ingest_fee_revenue_snapshots()

    assert result["succeeded"] == 1
    assert result["revenue_recorded"] == 0
    assert FeeSnapshot.objects.count() == 1
    assert RevenueSnapshot.objects.count() == 0


@responses.activate
def test_one_protocol_failure_does_not_block_others():
    make_protocol("uniswap")
    make_protocol("broken-slug")
    responses.add(responses.GET, f"{DEFILLAMA_BASE_URL}/summary/fees/uniswap", json=WITH_REVENUE, status=200)
    responses.add(responses.GET, f"{DEFILLAMA_BASE_URL}/summary/fees/broken-slug", json={}, status=404)

    result = ingest_fee_revenue_snapshots()

    assert result["succeeded"] == 1
    assert "broken-slug" in result["failed"]
    job = DataIngestionJob.objects.get()
    assert job.status == DataIngestionJob.Status.PARTIAL


def test_no_protocols_succeeds_with_zero_attempted():
    result = ingest_fee_revenue_snapshots()
    assert result == {"attempted": 0, "succeeded": 0}
