import pytest
import responses

from core.models import Asset, Blockchain, ContractAddress, DataIngestionJob, HolderSnapshot
from core.providers.onchain import COINGECKO_BASE_URL
from core.tasks.holder_ingestion import ingest_holder_snapshots

pytestmark = pytest.mark.django_db

ADDRESS = "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"

FULL_PAYLOAD = {
    "data": {"attributes": {"holders": {"count": 1000, "distribution_percentage": {"top_10": "40.0"}}}}
}
NO_HOLDERS_PAYLOAD = {"data": {"attributes": {}}}


def make_contract(chain_slug="ethereum", address=ADDRESS):
    asset = Asset.objects.create(symbol="uni", name="Uniswap", external_ids={"coingecko": "uniswap"})
    chain = Blockchain.objects.create(slug=chain_slug, name=chain_slug.title())
    return ContractAddress.objects.create(asset=asset, blockchain=chain, address=address)


@responses.activate
def test_ingests_holder_snapshot_for_mapped_chain():
    make_contract()
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/onchain/networks/eth/tokens/{ADDRESS}/info",
        json=FULL_PAYLOAD, status=200,
    )
    result = ingest_holder_snapshots()
    assert result["succeeded"] == 1
    assert HolderSnapshot.objects.count() == 1
    snap = HolderSnapshot.objects.get()
    assert snap.holder_count == 1000
    assert snap.top_10_concentration_pct == 40


def test_unmapped_chain_is_skipped_not_failed():
    make_contract(chain_slug="some-unmapped-chain")
    result = ingest_holder_snapshots()
    assert result["attempted"] == 0
    assert result["unmapped_chains"] == 1
    job = DataIngestionJob.objects.get()
    assert job.status == DataIngestionJob.Status.SUCCESS


@responses.activate
def test_no_holders_data_is_not_counted_as_failure():
    make_contract()
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/onchain/networks/eth/tokens/{ADDRESS}/info",
        json=NO_HOLDERS_PAYLOAD, status=200,
    )
    result = ingest_holder_snapshots()
    assert result["no_data"] == 1
    assert result["succeeded"] == 0
    assert HolderSnapshot.objects.count() == 0
    job = DataIngestionJob.objects.get()
    # Zero succeeded out of one attempted -> PARTIAL, not FAILED, since
    # "no data available" isn't the same as an actual error.
    assert job.status == DataIngestionJob.Status.PARTIAL


def test_no_contract_addresses_succeeds_with_zero_attempted():
    result = ingest_holder_snapshots()
    assert result["attempted"] == 0
    assert result["succeeded"] == 0
