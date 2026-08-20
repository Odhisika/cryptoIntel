from io import StringIO

import pytest
import responses
from django.core.management import call_command

from core.models import Asset
from core.providers.coingecko import COINGECKO_BASE_URL

pytestmark = pytest.mark.django_db


@responses.activate
def test_classifies_asset_from_categories():
    Asset.objects.create(symbol="eth", name="Ethereum", external_ids={"coingecko": "ethereum"})
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/coins/ethereum",
        json={"id": "ethereum", "categories": ["Layer 1 (L1)", "Smart Contract Platform"]}, status=200,
    )

    out = StringIO()
    call_command("populate_sectors", stdout=out)

    asset = Asset.objects.get()
    assert asset.sector == Asset.Sector.L1
    assert "1 classified" in out.getvalue()


@responses.activate
def test_unmatched_categories_leave_sector_null():
    Asset.objects.create(symbol="xyz", name="Unknown", external_ids={"coingecko": "unknown-coin"})
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/coins/unknown-coin",
        json={"id": "unknown-coin", "categories": ["FTX Holdings"]}, status=200,
    )

    call_command("populate_sectors")

    asset = Asset.objects.get()
    assert asset.sector is None
    assert asset.raw_categories == ["FTX Holdings"]


@responses.activate
def test_already_classified_assets_are_skipped_without_force():
    Asset.objects.create(
        symbol="eth", name="Ethereum", external_ids={"coingecko": "ethereum"}, sector=Asset.Sector.L1
    )

    call_command("populate_sectors")

    # No HTTP call should have been made — asset already has a sector.
    assert len(responses.calls) == 0


@responses.activate
def test_force_reclassifies_existing_assets():
    Asset.objects.create(
        symbol="eth", name="Ethereum", external_ids={"coingecko": "ethereum"}, sector=Asset.Sector.MEME
    )
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/coins/ethereum",
        json={"id": "ethereum", "categories": ["Layer 1 (L1)"]}, status=200,
    )

    call_command("populate_sectors", "--force")

    asset = Asset.objects.get()
    assert asset.sector == Asset.Sector.L1


@responses.activate
def test_dry_run_does_not_write():
    Asset.objects.create(symbol="eth", name="Ethereum", external_ids={"coingecko": "ethereum"})
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/coins/ethereum",
        json={"id": "ethereum", "categories": ["Layer 1 (L1)"]}, status=200,
    )

    call_command("populate_sectors", "--dry-run")

    asset = Asset.objects.get()
    assert asset.sector is None
