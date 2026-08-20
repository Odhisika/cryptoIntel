"""
Tokenomics math (section 18) — the subset computable from data this
codebase already ingests (CoinGecko's circulating/total/max supply
fields on MarketSnapshot), with NO new provider or API call needed.

WHAT THIS DOES NOT COVER, and why: section 18 also asks for team/investor/
foundation/community/treasury/ecosystem allocation percentages and actual
unlock dates/amounts. Every allocation/unlock-schedule data source found
during Phase 4 research (DefiLlama's `/api/emissions` and Tokenomist.ai's
API) requires a paid subscription ($300/mo and API-key-gated respectively,
confirmed against their own docs on 2026-08-08) — none is genuinely free.
Per this codebase's standing rule against fabricating provider
integrations, that data stays out of scope here. `token_unlock_risk` in
the Risk Score remains insufficient_data for exactly this reason,
documented there and in docs/DATA_LICENSING.md's "not integrated" list.

What IS real here:
- circulating/total and circulating/max supply ratios — a genuine
  tokenomics signal independent of price: a low circ/max ratio means most
  of the theoretical maximum supply hasn't entered circulation yet,
  regardless of what FDV/market-cap (a PRICE-weighted measure) says.
  These two measures can disagree — a token can look fine on FDV/MC (if
  the market has already priced in expected dilution) while still having
  a low circ/max ratio, or vice versa. Both are tracked and used as
  independent inputs rather than assuming one implies the other.
- Realized supply inflation over trailing 12M/24M windows, computed from
  MarketSnapshot's own history of total_supply — the same
  baseline-lookup-with-tolerance pattern Momentum Score uses for price
  returns. This will report insufficient_data for any asset that hasn't
  been tracked for 12+ months yet, which is the honest answer, not a bug
  (same as Momentum's 90D window on a newly-added asset).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from core.models import Asset, MarketSnapshot
from core.scoring.snapshot_lookup import find_baseline_snapshot


@dataclass(frozen=True)
class SupplyRatios:
    circulating_to_total_pct: Optional[Decimal]
    circulating_to_max_pct: Optional[Decimal]


def compute_supply_ratios(snapshot: MarketSnapshot) -> SupplyRatios:
    circ = snapshot.circulating_supply
    total = snapshot.total_supply
    max_supply = snapshot.max_supply

    circ_to_total = None
    if circ is not None and total and total > 0:
        circ_to_total = min(circ / total, Decimal("1")) * Decimal("100")

    circ_to_max = None
    if circ is not None and max_supply and max_supply > 0:
        circ_to_max = min(circ / max_supply, Decimal("1")) * Decimal("100")

    return SupplyRatios(circulating_to_total_pct=circ_to_total, circulating_to_max_pct=circ_to_max)


def compute_supply_inflation_pct(asset: Asset, current: MarketSnapshot, months_back: int) -> Optional[Decimal]:
    """% change in total_supply over the trailing N months, using the
    same tolerance-windowed baseline lookup Momentum Score uses. Returns
    None (insufficient_data to the caller) if there's no snapshot far
    enough back, or if either supply figure is missing."""

    if current.total_supply is None or current.total_supply <= 0:
        return None

    baseline = find_baseline_snapshot(asset, current.observed_at, months_back * 30)
    if baseline is None or baseline.total_supply is None or baseline.total_supply <= 0:
        return None

    return ((current.total_supply - baseline.total_supply) / baseline.total_supply) * Decimal("100")
