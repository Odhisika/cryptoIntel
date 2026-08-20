from decimal import Decimal

from core.models import Asset
from core.scoring.sector_weights import is_tvl_relevant, redistribute_weights


def test_tvl_relevant_sectors():
    assert is_tvl_relevant(Asset.Sector.DEFI) is True
    assert is_tvl_relevant(Asset.Sector.DEX) is True
    assert is_tvl_relevant(Asset.Sector.LENDING) is True
    assert is_tvl_relevant(Asset.Sector.DERIVATIVES) is True
    assert is_tvl_relevant(Asset.Sector.STABLECOIN_INFRA) is True


def test_tvl_not_relevant_sectors():
    assert is_tvl_relevant(Asset.Sector.L1) is False
    assert is_tvl_relevant(Asset.Sector.MEME) is False
    assert is_tvl_relevant(Asset.Sector.DEPIN) is False
    assert is_tvl_relevant(Asset.Sector.GAMING) is False


def test_unclassified_asset_defaults_to_tvl_relevant():
    assert is_tvl_relevant(None) is True


def test_redistribute_weights_preserves_total():
    base = {"a": Decimal("50"), "b": Decimal("30"), "c": Decimal("20")}
    result = redistribute_weights(base, {"a"})
    assert sum(result.values()) == Decimal("100")


def test_redistribute_weights_zeroes_target_factor():
    base = {"a": Decimal("50"), "b": Decimal("30"), "c": Decimal("20")}
    result = redistribute_weights(base, {"a"})
    assert result["a"] == Decimal("0")


def test_redistribute_weights_proportional_to_remaining():
    base = {"a": Decimal("40"), "b": Decimal("30"), "c": Decimal("30")}
    result = redistribute_weights(base, {"a"})
    # b and c had equal weight (30 each) among the remaining 60, so each
    # should get half of the freed 40.
    assert result["b"] == Decimal("50")
    assert result["c"] == Decimal("50")


def test_redistribute_multiple_factors():
    base = {"a": Decimal("20"), "b": Decimal("20"), "c": Decimal("60")}
    result = redistribute_weights(base, {"a", "b"})
    assert result["a"] == Decimal("0")
    assert result["b"] == Decimal("0")
    assert result["c"] == Decimal("100")
    assert sum(result.values()) == Decimal("100")


def test_redistribute_ignores_unknown_names():
    base = {"a": Decimal("50"), "b": Decimal("50")}
    result = redistribute_weights(base, {"a", "nonexistent"})
    assert sum(result.values()) == Decimal("100")
    assert result["b"] == Decimal("100")


def test_redistribute_all_factors_zeroed_returns_unchanged():
    # Nothing left to redistribute onto — return the base weights as-is
    # rather than producing an all-zero result.
    base = {"a": Decimal("50"), "b": Decimal("50")}
    result = redistribute_weights(base, {"a", "b"})
    assert result == base
