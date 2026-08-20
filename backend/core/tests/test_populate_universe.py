from decimal import Decimal
from io import StringIO

import pytest
import responses
from django.core.management import call_command

from core.models import Asset
from core.providers.coingecko import COINGECKO_BASE_URL

pytestmark = pytest.mark.django_db

MARKETS_LIST_PAGE = [
    {"id": "mid-coin", "market_cap": 500_000_000},
]

MARKETS_DETAIL = [
    {
        "id": "mid-coin",
        "symbol": "mid",
        "name": "Mid Coin",
        "current_price": 2.5,
        "market_cap": 500_000_000,
        "fully_diluted_valuation": 600_000_000,
        "total_volume": 10_000_000,
        "circulating_supply": 200_000_000,
        "total_supply": 240_000_000,
        "max_supply": 300_000_000,
    }
]


@responses.activate
def test_populate_universe_creates_new_assets():
    responses.add(responses.GET, f"{COINGECKO_BASE_URL}/coins/markets", json=MARKETS_LIST_PAGE, status=200)
    responses.add(responses.GET, f"{COINGECKO_BASE_URL}/coins/markets", json=MARKETS_DETAIL, status=200)

    out = StringIO()
    call_command(
        "populate_universe", "--min-market-cap=100000000", "--max-market-cap=1000000000", stdout=out
    )

    assert Asset.objects.count() == 1
    asset = Asset.objects.get()
    assert asset.external_ids["coingecko"] == "mid-coin"
    assert asset.symbol == "mid"
    assert "1 to create" in out.getvalue()


@responses.activate
def test_populate_universe_dry_run_does_not_write():
    responses.add(responses.GET, f"{COINGECKO_BASE_URL}/coins/markets", json=MARKETS_LIST_PAGE, status=200)
    responses.add(responses.GET, f"{COINGECKO_BASE_URL}/coins/markets", json=MARKETS_DETAIL, status=200)

    out = StringIO()
    call_command(
        "populate_universe",
        "--min-market-cap=100000000",
        "--max-market-cap=1000000000",
        "--dry-run",
        stdout=out,
    )

    assert Asset.objects.count() == 0
    assert "DRY RUN" in out.getvalue()


@responses.activate
def test_populate_universe_is_idempotent_on_rerun():
    responses.add(responses.GET, f"{COINGECKO_BASE_URL}/coins/markets", json=MARKETS_LIST_PAGE, status=200)
    responses.add(responses.GET, f"{COINGECKO_BASE_URL}/coins/markets", json=MARKETS_DETAIL, status=200)
    call_command("populate_universe", "--min-market-cap=100000000", "--max-market-cap=1000000000")

    responses.add(responses.GET, f"{COINGECKO_BASE_URL}/coins/markets", json=MARKETS_LIST_PAGE, status=200)
    responses.add(responses.GET, f"{COINGECKO_BASE_URL}/coins/markets", json=MARKETS_DETAIL, status=200)
    call_command("populate_universe", "--min-market-cap=100000000", "--max-market-cap=1000000000")

    # Second run must not create a duplicate Asset for the same coingecko id.
    assert Asset.objects.count() == 1


def test_populate_universe_rejects_invalid_market_cap_band():
    with pytest.raises(Exception):
        call_command("populate_universe", "--min-market-cap=1000", "--max-market-cap=100")
