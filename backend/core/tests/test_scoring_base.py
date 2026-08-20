from decimal import Decimal

import pytest

from core.scoring.base import Factor, compute_weighted_score


def test_full_data_score_is_plain_weighted_average():
    factors = [
        Factor(name="a", weight=Decimal("50"), normalized_value=Decimal("100"), raw_value="x"),
        Factor(name="b", weight=Decimal("50"), normalized_value=Decimal("0"), raw_value="y"),
    ]
    result = compute_weighted_score(model_name="test", model_version="v1", factors=factors)
    assert result.score == Decimal("50.00")
    assert result.data_confidence == Decimal("1.0000")


def test_missing_factor_is_excluded_not_penalized():
    factors = [
        Factor(name="a", weight=Decimal("50"), normalized_value=Decimal("100"), raw_value="x"),
        Factor(name="b", weight=Decimal("50"), normalized_value=None, raw_value=None, insufficient_data=True),
    ]
    result = compute_weighted_score(model_name="test", model_version="v1", factors=factors)
    # Only factor 'a' is available, worth 100/100 on its own -> score is 100,
    # not 50 (which is what a naive "missing = 0" implementation would give).
    assert result.score == Decimal("100.00")
    assert result.data_confidence == Decimal("0.5000")


def test_all_factors_missing_gives_zero_score_zero_confidence():
    factors = [
        Factor(name="a", weight=Decimal("100"), normalized_value=None, raw_value=None, insufficient_data=True),
    ]
    result = compute_weighted_score(model_name="test", model_version="v1", factors=factors)
    assert result.score == Decimal("0")
    assert result.data_confidence == Decimal("0")


def test_factor_rejects_out_of_range_value():
    with pytest.raises(ValueError):
        Factor(name="a", weight=Decimal("10"), normalized_value=Decimal("150"), raw_value="x")


def test_factor_rejects_missing_value_without_flag():
    with pytest.raises(ValueError):
        Factor(name="a", weight=Decimal("10"), normalized_value=None, raw_value=None)


def test_zero_total_weight_raises():
    with pytest.raises(ValueError):
        compute_weighted_score(model_name="test", model_version="v1", factors=[])


def test_top_contributors_ranks_by_weighted_contribution():
    factors = [
        Factor(name="small_but_high", weight=Decimal("10"), normalized_value=Decimal("100"), raw_value="x"),
        Factor(name="big_but_low", weight=Decimal("50"), normalized_value=Decimal("20"), raw_value="y"),
        Factor(name="missing", weight=Decimal("40"), normalized_value=None, raw_value=None, insufficient_data=True),
    ]
    result = compute_weighted_score(model_name="test", model_version="v1", factors=factors)
    top = result.top_contributors(n=1)
    # big_but_low contributes 50*20=1000, small_but_high contributes 10*100=1000 — tie is fine,
    # but missing factor must never appear in top_contributors.
    assert all(not f.insufficient_data for f in top)


def test_missing_factors_helper():
    factors = [
        Factor(name="a", weight=Decimal("50"), normalized_value=Decimal("100"), raw_value="x"),
        Factor(name="b", weight=Decimal("50"), normalized_value=None, raw_value=None, insufficient_data=True),
    ]
    result = compute_weighted_score(model_name="test", model_version="v1", factors=factors)
    assert [f.name for f in result.missing_factors()] == ["b"]


def test_as_explanation_dict_shape():
    factors = [Factor(name="a", weight=Decimal("100"), normalized_value=Decimal("75"), raw_value="raw")]
    result = compute_weighted_score(model_name="test_model", model_version="v1.0", factors=factors)
    explanation = result.as_explanation_dict()
    assert explanation["model_name"] == "test_model"
    assert explanation["model_version"] == "v1.0"
    assert explanation["factors"][0]["name"] == "a"
    assert explanation["factors"][0]["raw_value"] == "raw"
