from io import StringIO

import pytest
import responses
from django.core.management import call_command

from core.models import Asset, Protocol
from core.providers.defillama import DEFILLAMA_BASE_URL

pytestmark = pytest.mark.django_db

PROTOCOLS_PAYLOAD = [
    {"slug": "uniswap", "name": "Uniswap", "gecko_id": "uniswap", "category": "Dexes",
     "tvl": 5000000000, "chains": ["Ethereum"]},
    {"slug": "unmatched-protocol", "name": "Unmatched", "gecko_id": "some-other-coin",
     "category": "Lending", "tvl": 1000000, "chains": ["Ethereum"]},
]


@responses.activate
def test_matches_protocol_to_asset_via_gecko_id():
    Asset.objects.create(symbol="uni", name="Uniswap", external_ids={"coingecko": "uniswap"})
    responses.add(responses.GET, f"{DEFILLAMA_BASE_URL}/protocols", json=PROTOCOLS_PAYLOAD, status=200)

    out = StringIO()
    call_command("populate_protocols", stdout=out)

    assert Protocol.objects.count() == 1
    protocol = Protocol.objects.get()
    assert protocol.slug == "uniswap"
    assert protocol.asset.symbol == "uni"
    assert "1 matched" in out.getvalue()


@responses.activate
def test_unmatched_protocol_is_not_created():
    # No Asset exists with gecko_id "some-other-coin" or "uniswap".
    responses.add(responses.GET, f"{DEFILLAMA_BASE_URL}/protocols", json=PROTOCOLS_PAYLOAD, status=200)

    call_command("populate_protocols")

    assert Protocol.objects.count() == 0


@responses.activate
def test_dry_run_does_not_write():
    Asset.objects.create(symbol="uni", name="Uniswap", external_ids={"coingecko": "uniswap"})
    responses.add(responses.GET, f"{DEFILLAMA_BASE_URL}/protocols", json=PROTOCOLS_PAYLOAD, status=200)

    out = StringIO()
    call_command("populate_protocols", "--dry-run", stdout=out)

    assert Protocol.objects.count() == 0
    assert "DRY RUN" in out.getvalue()


@responses.activate
def test_idempotent_on_rerun():
    Asset.objects.create(symbol="uni", name="Uniswap", external_ids={"coingecko": "uniswap"})
    responses.add(responses.GET, f"{DEFILLAMA_BASE_URL}/protocols", json=PROTOCOLS_PAYLOAD, status=200)
    call_command("populate_protocols")

    responses.add(responses.GET, f"{DEFILLAMA_BASE_URL}/protocols", json=PROTOCOLS_PAYLOAD, status=200)
    call_command("populate_protocols")

    assert Protocol.objects.count() == 1
