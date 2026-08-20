import pytest
import responses

from core.models import DataIngestionJob, Protocol, TVLSnapshot
from core.providers.defillama import DEFILLAMA_BASE_URL
from core.tasks.tvl_ingestion import ingest_tvl_snapshots

pytestmark = pytest.mark.django_db

DETAIL_PAYLOAD = {
    "name": "Uniswap",
    "tvl": [
        {"date": 1704672000, "totalLiquidityUSD": 4500000000},
        {"date": 1704758400, "totalLiquidityUSD": 5000000000},
    ],
}


def make_protocol(slug="uniswap"):
    return Protocol.objects.create(slug=slug, name=slug.title())


def test_no_protocols_succeeds_with_zero_attempted():
    result = ingest_tvl_snapshots()
    assert result == {"attempted": 0, "succeeded": 0}
    job = DataIngestionJob.objects.get()
    assert job.status == DataIngestionJob.Status.SUCCESS


@responses.activate
def test_ingests_tvl_snapshot_for_active_protocol():
    make_protocol()
    responses.add(responses.GET, f"{DEFILLAMA_BASE_URL}/protocol/uniswap", json=DETAIL_PAYLOAD, status=200)

    result = ingest_tvl_snapshots()

    assert result["succeeded"] == 1
    assert TVLSnapshot.objects.count() == 1
    snap = TVLSnapshot.objects.get()
    assert snap.tvl_usd == 5000000000


@responses.activate
def test_inactive_protocol_is_not_ingested():
    protocol = make_protocol()
    protocol.is_active = False
    protocol.save()

    result = ingest_tvl_snapshots()

    assert result["attempted"] == 0
    assert TVLSnapshot.objects.count() == 0


@responses.activate
def test_one_protocol_failure_does_not_block_others():
    make_protocol("uniswap")
    make_protocol("broken-slug")

    responses.add(responses.GET, f"{DEFILLAMA_BASE_URL}/protocol/uniswap", json=DETAIL_PAYLOAD, status=200)
    responses.add(responses.GET, f"{DEFILLAMA_BASE_URL}/protocol/broken-slug", json={}, status=404)

    result = ingest_tvl_snapshots()

    assert result["succeeded"] == 1
    assert "broken-slug" in result["failed"]
    job = DataIngestionJob.objects.get()
    assert job.status == DataIngestionJob.Status.PARTIAL


@responses.activate
def test_rerun_does_not_duplicate_snapshot_for_same_timestamp():
    make_protocol()
    responses.add(responses.GET, f"{DEFILLAMA_BASE_URL}/protocol/uniswap", json=DETAIL_PAYLOAD, status=200)
    ingest_tvl_snapshots()

    responses.add(responses.GET, f"{DEFILLAMA_BASE_URL}/protocol/uniswap", json=DETAIL_PAYLOAD, status=200)
    ingest_tvl_snapshots()

    assert TVLSnapshot.objects.count() == 1
