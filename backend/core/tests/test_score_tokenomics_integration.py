from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.models import Asset, MarketSnapshot
from core.scoring.potential_10x import compute_10x_potential_score
from core.scoring.risk import compute_risk_score

pytestmark = pytest.mark.django_db


def make_asset():
    return Asset.objects.create(symbol="tst", name="Test Coin", external_ids={"coingecko": "test"})


def snap(asset, days_ago=0, circ=None, total=None, max_supply=None, fdv=None, mc=Decimal("100000000")):
    return MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("1"), market_cap_usd=mc,
        fully_diluted_valuation_usd=fdv,
        circulating_supply=circ, total_supply=total, max_supply=max_supply,
        source="coingecko",
        observed_at=datetime(2027, 1, 1, tzinfo=timezone.utc) - timedelta(days=days_ago),
    )


# --- 10X Potential: tokenomics factor ---

def test_tokenomics_insufficient_without_max_supply():
    asset = make_asset()
    s = snap(asset, circ=Decimal("500000"), total=Decimal("800000"))
    result = compute_10x_potential_score(asset, s)
    tok = next(f for f in result.factors if f.name == "tokenomics")
    assert tok.insufficient_data is True


def test_tokenomics_uses_circ_max_ratio_when_no_inflation_data():
    asset = make_asset()
    s = snap(asset, circ=Decimal("900000"), total=Decimal("900000"), max_supply=Decimal("1000000"))
    result = compute_10x_potential_score(asset, s)
    tok = next(f for f in result.factors if f.name == "tokenomics")
    assert tok.insufficient_data is False
    assert tok.normalized_value == Decimal("90")


def test_tokenomics_penalized_by_high_inflation():
    asset = make_asset()
    snap(asset, days_ago=360, total=Decimal("800000"))
    current = snap(asset, days_ago=0, circ=Decimal("900000"), total=Decimal("1000000"),
                    max_supply=Decimal("1000000"))
    result = compute_10x_potential_score(asset, current)
    tok = next(f for f in result.factors if f.name == "tokenomics")
    # circ/max = 90, minus a 25% inflation penalty -> 65
    assert tok.normalized_value == Decimal("65")


def test_tokenomics_inflation_penalty_never_goes_negative():
    asset = make_asset()
    snap(asset, days_ago=365, total=Decimal("100000"))
    current = snap(asset, days_ago=0, circ=Decimal("100000"), total=Decimal("1000000"),
                    max_supply=Decimal("1000000"))  # 900% inflation
    result = compute_10x_potential_score(asset, current)
    tok = next(f for f in result.factors if f.name == "tokenomics")
    assert tok.normalized_value >= Decimal("0")


# --- Risk Score: enhanced dilution_risk ---

def test_dilution_risk_uses_worse_of_fdv_and_supply_signal():
    asset = make_asset()
    # FDV/MC gap is mild (20%), but supply gap is severe (80% not yet circulating).
    s = snap(asset, circ=Decimal("200000"), max_supply=Decimal("1000000"),
              fdv=Decimal("125000000"), mc=Decimal("100000000"))
    result = compute_risk_score(asset, s)
    dilution = next(f for f in result.factors if f.name == "dilution_risk")
    # FDV gap = (125M-100M)/125M = 20%. Supply gap = 100 - 20 = 80%. Should take the worse (80).
    assert dilution.normalized_value == Decimal("80")


def test_dilution_risk_falls_back_to_supply_signal_without_fdv():
    asset = make_asset()
    s = snap(asset, circ=Decimal("300000"), max_supply=Decimal("1000000"), fdv=None)
    result = compute_risk_score(asset, s)
    dilution = next(f for f in result.factors if f.name == "dilution_risk")
    assert dilution.insufficient_data is False
    assert dilution.normalized_value == Decimal("70")  # 100 - 30


def test_dilution_risk_insufficient_without_either_signal():
    asset = make_asset()
    s = snap(asset, fdv=None, max_supply=None)
    result = compute_risk_score(asset, s)
    dilution = next(f for f in result.factors if f.name == "dilution_risk")
    assert dilution.insufficient_data is True


def test_token_unlock_risk_always_insufficient_no_free_source():
    asset = make_asset()
    s = snap(asset)
    result = compute_risk_score(asset, s)
    unlock = next(f for f in result.factors if f.name == "token_unlock_risk")
    assert unlock.insufficient_data is True
    assert "paid subscription" in unlock.note
