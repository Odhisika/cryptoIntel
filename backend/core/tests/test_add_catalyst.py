from datetime import date, timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import Asset, Catalyst

pytestmark = pytest.mark.django_db


def make_asset():
    return Asset.objects.create(symbol="btc", name="Bitcoin")


def test_add_catalyst_creates_record():
    make_asset()
    future_date = (date.today() + timedelta(days=30)).isoformat()

    call_command(
        "add_catalyst", "BTC",
        "--title", "ETF Decision Deadline",
        "--description", "SEC final deadline to rule on spot ETF applications.",
        "--type", "governance_event",
        "--date", future_date,
        "--source", "https://www.sec.gov/example",
        "--confidence", "confirmed",
        "--impact", "high",
    )

    catalyst = Catalyst.objects.get()
    assert catalyst.title == "ETF Decision Deadline"
    assert catalyst.status == Catalyst.Status.UPCOMING
    assert catalyst.confidence == Catalyst.Confidence.CONFIRMED


def test_add_catalyst_rejects_unknown_asset():
    with pytest.raises(CommandError):
        call_command(
            "add_catalyst", "NOTREAL",
            "--title", "x", "--description", "x", "--type", "mainnet",
            "--date", "2027-01-01", "--source", "https://example.com",
            "--confidence", "confirmed", "--impact", "low",
        )


def test_add_catalyst_rejects_non_url_source():
    make_asset()
    with pytest.raises(CommandError):
        call_command(
            "add_catalyst", "BTC",
            "--title", "x", "--description", "x", "--type", "mainnet",
            "--date", "2027-01-01", "--source", "not-a-url",
            "--confidence", "confirmed", "--impact", "low",
        )


def test_add_catalyst_rejects_bad_date_format():
    make_asset()
    with pytest.raises(CommandError):
        call_command(
            "add_catalyst", "BTC",
            "--title", "x", "--description", "x", "--type", "mainnet",
            "--date", "01/01/2027", "--source", "https://example.com",
            "--confidence", "confirmed", "--impact", "low",
        )


def test_catalyst_str_includes_key_fields():
    asset = make_asset()
    catalyst = Catalyst.objects.create(
        asset=asset, title="Mainnet Launch", description="x",
        catalyst_type=Catalyst.CatalystType.MAINNET, event_date=date(2027, 1, 1),
        source_url="https://example.com", confidence=Catalyst.Confidence.CONFIRMED,
        impact_estimate="high", status=Catalyst.Status.UPCOMING,
    )
    assert "btc" in str(catalyst)
    assert "Mainnet Launch" in str(catalyst)
