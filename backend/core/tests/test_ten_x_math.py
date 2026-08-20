from decimal import Decimal

import pytest

from core.scoring.ten_x_math import compute_10x_math


def test_basic_10x_math():
    result = compute_10x_math(
        current_market_cap_usd=Decimal("80000000"),
        current_price_usd=Decimal("0.50"),
    )
    assert result.target_market_cap_usd == Decimal("800000000")
    assert result.required_market_cap_growth_pct == Decimal("900")
    assert result.required_token_price_usd == Decimal("5.00")


def test_custom_multiple():
    result = compute_10x_math(
        current_market_cap_usd=Decimal("100000000"),
        current_price_usd=Decimal("1"),
        target_multiple=Decimal("5"),
    )
    assert result.target_market_cap_usd == Decimal("500000000")
    assert result.required_market_cap_growth_pct == Decimal("400")


def test_fdv_gap_computed_when_fdv_present():
    result = compute_10x_math(
        current_market_cap_usd=Decimal("80000000"),
        current_price_usd=Decimal("0.50"),
        current_fdv_usd=Decimal("160000000"),  # 2x current MC — heavy dilution overhang
    )
    assert result.target_fdv_usd == Decimal("1600000000")
    # Target FDV is 2x target MC, so gap should be 100%.
    assert result.fdv_market_cap_gap_pct == Decimal("100")


def test_fdv_gap_is_none_without_fdv_input():
    result = compute_10x_math(
        current_market_cap_usd=Decimal("80000000"),
        current_price_usd=Decimal("0.50"),
    )
    assert result.target_fdv_usd is None
    assert result.fdv_market_cap_gap_pct is None


@pytest.mark.parametrize(
    "market_cap,price",
    [
        (Decimal("0"), Decimal("1")),
        (Decimal("-1"), Decimal("1")),
        (Decimal("100"), Decimal("0")),
        (Decimal("100"), Decimal("-1")),
    ],
)
def test_non_positive_inputs_raise(market_cap, price):
    with pytest.raises(ValueError):
        compute_10x_math(current_market_cap_usd=market_cap, current_price_usd=price)
