"""
Comparable project engine (section 22).

Candidate -> Sector -> Market-cap bracket -> Peer group -> Multiples.

Deliberately NOT wired into any of the 4 scores yet. The spec assigns
this capability its own section but doesn't give it an explicit weight
inside the existing 4 score models' factor lists — folding it into e.g.
Undervaluation's fixed 100-weight budget would mean inventing a weight
rebalance the spec doesn't actually call for. Better to ship this as a
standalone, well-tested capability now (usable by the CLI today, and
naturally by Phase 9's AI research engine or Phase 11's dashboard later)
than to force a premature, arbitrary scoring integration.

Chain matching (the last step in the spec's diagram) is not implemented
here — most assets in this database don't yet have a clean single
"primary chain" concept (some are multi-chain via ContractAddress).
Sector + market-cap bracket is the meaningful peer filter for now; chain
narrowing is a reasonable future refinement once it's clear peer groups
are too broad without it.
"""

import statistics
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from core.models import Asset, MarketSnapshot, Protocol


@dataclass(frozen=True)
class PeerSnapshot:
    asset_id: str
    symbol: str
    market_cap_usd: Decimal
    tvl_usd: Optional[Decimal]
    revenue_annualized_usd: Optional[Decimal]


@dataclass(frozen=True)
class ComparablesResult:
    candidate_symbol: str
    sector: str
    candidate_market_cap_usd: Decimal
    peer_count: int
    peer_median_market_cap_usd: Optional[Decimal]
    peer_average_market_cap_usd: Optional[Decimal]
    candidate_tvl_multiple: Optional[Decimal]  # candidate MC / candidate TVL
    peer_median_tvl_multiple: Optional[Decimal]
    candidate_revenue_multiple: Optional[Decimal]  # candidate MC / candidate annualized revenue
    peer_median_revenue_multiple: Optional[Decimal]
    peer_user_multiple_note: str  # always "insufficient_data" until a user-activity source exists

    def market_cap_vs_peer_median_pct(self) -> Optional[Decimal]:
        """How far the candidate's market cap sits from the peer median,
        as a percentage (positive = candidate is priced above peers)."""
        if not self.peer_median_market_cap_usd or self.peer_median_market_cap_usd <= 0:
            return None
        return (
            (self.candidate_market_cap_usd - self.peer_median_market_cap_usd)
            / self.peer_median_market_cap_usd
        ) * Decimal("100")


def _latest_market_cap(asset: Asset) -> Optional[Decimal]:
    latest = asset.market_snapshots.order_by("-observed_at").first()
    return latest.market_cap_usd if latest else None


def _latest_tvl(asset: Asset) -> Optional[Decimal]:
    protocol = Protocol.objects.filter(asset=asset, is_active=True).first()
    if protocol is None:
        return None
    snap = protocol.tvl_snapshots.order_by("-observed_at").first()
    return snap.tvl_usd if snap else None


def _latest_annualized_revenue(asset: Asset) -> Optional[Decimal]:
    protocol = Protocol.objects.filter(asset=asset, is_active=True).first()
    if protocol is None:
        return None
    snap = protocol.revenue_snapshots.order_by("-observed_at").first()
    if snap is None:
        return None
    return snap.revenue_24h_usd * Decimal("365")


def find_comparables(
    asset: Asset, *, bracket_ratio: Decimal = Decimal("3"), max_peers: int = 50
) -> Optional[ComparablesResult]:
    """Returns None if the candidate has no sector or no market cap to
    bracket against — there's no meaningful peer group to compute without
    both. A result with peer_count=0 (sector assigned, but no other asset
    in that sector/bracket) is a real, valid result, distinct from None."""

    if not asset.sector:
        return None

    candidate_mc = _latest_market_cap(asset)
    if not candidate_mc or candidate_mc <= 0:
        return None

    low = candidate_mc / bracket_ratio
    high = candidate_mc * bracket_ratio

    candidates = Asset.objects.filter(sector=asset.sector, is_active=True).exclude(id=asset.id)

    peers: list[PeerSnapshot] = []
    for candidate in candidates:
        mc = _latest_market_cap(candidate)
        if mc is None or not (low <= mc <= high):
            continue
        peers.append(
            PeerSnapshot(
                asset_id=str(candidate.id),
                symbol=candidate.symbol,
                market_cap_usd=mc,
                tvl_usd=_latest_tvl(candidate),
                revenue_annualized_usd=_latest_annualized_revenue(candidate),
            )
        )
        if len(peers) >= max_peers:
            break

    peer_mcs = [p.market_cap_usd for p in peers]
    peer_median_mc = Decimal(str(statistics.median(peer_mcs))) if peer_mcs else None
    peer_average_mc = (sum(peer_mcs) / len(peer_mcs)) if peer_mcs else None

    candidate_tvl = _latest_tvl(asset)
    candidate_tvl_multiple = (candidate_mc / candidate_tvl) if candidate_tvl and candidate_tvl > 0 else None
    peer_tvl_multiples = [
        p.market_cap_usd / p.tvl_usd for p in peers if p.tvl_usd and p.tvl_usd > 0
    ]
    peer_median_tvl_multiple = (
        Decimal(str(statistics.median(peer_tvl_multiples))) if peer_tvl_multiples else None
    )

    candidate_revenue = _latest_annualized_revenue(asset)
    candidate_revenue_multiple = (
        (candidate_mc / candidate_revenue) if candidate_revenue and candidate_revenue > 0 else None
    )
    peer_revenue_multiples = [
        p.market_cap_usd / p.revenue_annualized_usd
        for p in peers
        if p.revenue_annualized_usd and p.revenue_annualized_usd > 0
    ]
    peer_median_revenue_multiple = (
        Decimal(str(statistics.median(peer_revenue_multiples))) if peer_revenue_multiples else None
    )

    return ComparablesResult(
        candidate_symbol=asset.symbol,
        sector=asset.sector,
        candidate_market_cap_usd=candidate_mc,
        peer_count=len(peers),
        peer_median_market_cap_usd=peer_median_mc,
        peer_average_market_cap_usd=peer_average_mc,
        candidate_tvl_multiple=candidate_tvl_multiple,
        peer_median_tvl_multiple=peer_median_tvl_multiple,
        candidate_revenue_multiple=candidate_revenue_multiple,
        peer_median_revenue_multiple=peer_median_revenue_multiple,
        peer_user_multiple_note="insufficient_data — no user-activity data source identified yet",
    )
