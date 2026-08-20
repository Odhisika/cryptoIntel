from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.models import Asset, MarketSnapshot, Protocol, RevenueSnapshot, TVLSnapshot
from core.scoring.comparables import find_comparables

pytestmark = pytest.mark.django_db


def make_asset(symbol, market_cap, sector=Asset.Sector.DEX):
    asset = Asset.objects.create(symbol=symbol, name=symbol.upper(), sector=sector)
    MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("1"), market_cap_usd=market_cap,
        source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    return asset


def test_no_sector_returns_none():
    asset = make_asset("aaa", Decimal("100000000"), sector=None)
    assert find_comparables(asset) is None


def test_no_market_cap_returns_none():
    asset = Asset.objects.create(symbol="aaa", name="AAA", sector=Asset.Sector.DEX)
    assert find_comparables(asset) is None


def test_zero_peers_is_a_valid_result_not_none():
    asset = make_asset("aaa", Decimal("100000000"))
    result = find_comparables(asset)
    assert result is not None
    assert result.peer_count == 0
    assert result.peer_median_market_cap_usd is None


def test_peers_within_bracket_are_included():
    candidate = make_asset("aaa", Decimal("100000000"))
    make_asset("peer1", Decimal("150000000"))  # within 3x band
    make_asset("peer2", Decimal("50000000"))  # within 3x band
    make_asset("too_big", Decimal("500000000"))  # outside 3x band (candidate*3=300M)
    make_asset("too_small", Decimal("10000000"))  # outside band (candidate/3=33.3M)

    result = find_comparables(candidate)
    assert result.peer_count == 2


def test_different_sector_excluded():
    candidate = make_asset("aaa", Decimal("100000000"), sector=Asset.Sector.DEX)
    make_asset("other_sector", Decimal("100000000"), sector=Asset.Sector.L1)

    result = find_comparables(candidate)
    assert result.peer_count == 0


def test_self_excluded_from_peers():
    candidate = make_asset("aaa", Decimal("100000000"))
    result = find_comparables(candidate)
    assert candidate.symbol not in [p for p in []]  # sanity: no crash
    assert result.peer_count == 0


def test_peer_median_and_average_market_cap():
    candidate = make_asset("aaa", Decimal("100000000"))
    make_asset("peer1", Decimal("80000000"))
    make_asset("peer2", Decimal("120000000"))

    result = find_comparables(candidate)
    assert result.peer_count == 2
    assert result.peer_median_market_cap_usd == Decimal("100000000")
    assert result.peer_average_market_cap_usd == Decimal("100000000")


def test_market_cap_vs_peer_median_pct():
    candidate = make_asset("aaa", Decimal("150000000"))
    make_asset("peer1", Decimal("100000000"))

    result = find_comparables(candidate)
    # Candidate is 50% above the (single) peer's market cap.
    assert result.market_cap_vs_peer_median_pct() == Decimal("50")


def test_tvl_multiples_computed_when_available():
    candidate = make_asset("aaa", Decimal("100000000"))
    peer = make_asset("peer1", Decimal("100000000"))

    cand_protocol = Protocol.objects.create(asset=candidate, slug="aaa-protocol", name="AAA")
    TVLSnapshot.objects.create(
        protocol=cand_protocol, tvl_usd=Decimal("50000000"),
        source="defillama", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    peer_protocol = Protocol.objects.create(asset=peer, slug="peer1-protocol", name="Peer1")
    TVLSnapshot.objects.create(
        protocol=peer_protocol, tvl_usd=Decimal("25000000"),
        source="defillama", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    result = find_comparables(candidate)
    # candidate: 100M MC / 50M TVL = 2x. peer: 100M MC / 25M TVL = 4x.
    assert result.candidate_tvl_multiple == Decimal("2")
    assert result.peer_median_tvl_multiple == Decimal("4")


def test_revenue_multiples_computed_when_available():
    candidate = make_asset("aaa", Decimal("100000000"))
    peer = make_asset("peer1", Decimal("100000000"))

    cand_protocol = Protocol.objects.create(asset=candidate, slug="aaa-protocol", name="AAA")
    RevenueSnapshot.objects.create(
        protocol=cand_protocol, revenue_24h_usd=Decimal("10000"),  # 3.65M/yr
        source="defillama", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    result = find_comparables(candidate)
    assert result.candidate_revenue_multiple is not None
    assert result.peer_median_revenue_multiple is None  # peer has no revenue data


def test_no_tvl_data_gives_none_multiples_not_zero():
    candidate = make_asset("aaa", Decimal("100000000"))
    result = find_comparables(candidate)
    assert result.candidate_tvl_multiple is None
    assert result.peer_median_tvl_multiple is None


def test_user_multiple_always_reports_insufficient_data():
    candidate = make_asset("aaa", Decimal("100000000"))
    result = find_comparables(candidate)
    assert "insufficient_data" in result.peer_user_multiple_note


def test_max_peers_caps_peer_count():
    candidate = make_asset("aaa", Decimal("100000000"))
    for i in range(5):
        make_asset(f"peer{i}", Decimal("100000000"))

    result = find_comparables(candidate, max_peers=2)
    assert result.peer_count == 2
