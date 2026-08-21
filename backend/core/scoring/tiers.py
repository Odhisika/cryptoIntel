"""
Risk/reward tier classification.

After all 4 scores are computed, this module classifies each token into
one of 4 tiers based on the combination of upside potential and risk.

Tiers:
  - 2X Safe      — Low risk, reliable projects, moderate upside
  - 3X Growth    — Balanced risk/reward, solid fundamentals
  - 10X Potential — Higher risk, strong growth signals
  - Moonshot      — Very high risk, speculative, could 50x or go to zero

Classification is based on ALL 4 scores together, not just one.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional


class RewardTier(str, Enum):
    SAFE_2X = "2x_safe"
    GROWTH_3X = "3x_growth"
    POTENTIAL_10X = "10x_potential"
    MOONSHOT = "moonshot"


TIER_LABELS = {
    RewardTier.SAFE_2X: "2X Safe",
    RewardTier.GROWTH_3X: "3X Growth",
    RewardTier.POTENTIAL_10X: "10X Potential",
    RewardTier.MOONSHOT: "Moonshot",
}

TIER_DESCRIPTIONS = {
    RewardTier.SAFE_2X: "Lower risk, established projects with steady fundamentals. Expect moderate 2x returns over time.",
    RewardTier.GROWTH_3X: "Balanced risk/reward. Solid projects with room to grow 3x based on fundamentals and momentum.",
    RewardTier.POTENTIAL_10X: "Higher risk, high reward. Strong signals across multiple metrics — could deliver 10x returns.",
    RewardTier.MOONSHOT: "Speculative, very high risk. Could 50x or go to zero. Only for risk-tolerant traders.",
}


@dataclass(frozen=True)
class TierClassification:
    tier: RewardTier
    label: str
    description: str
    confidence: Decimal
    reasoning: list[str]


def classify_tier(
    *,
    score_10x: Optional[Decimal] = None,
    score_risk: Optional[Decimal] = None,
    score_momentum: Optional[Decimal] = None,
    score_undervaluation: Optional[Decimal] = None,
    data_confidence_10x: Decimal = Decimal("0"),
    data_confidence_risk: Decimal = Decimal("0"),
    data_confidence_momentum: Decimal = Decimal("0"),
    data_confidence_undervaluation: Decimal = Decimal("0"),
) -> TierClassification:
    """Classify a token into a reward tier based on its 4 scores.

    Scoring convention:
    - 10X Potential: higher = more upside potential (0-100)
    - Risk: HIGHER = MORE RISKY (0-100, opposite of other scores)
    - Momentum: higher = stronger trend (0-100)
    - Undervaluation: higher = more undervalued (0-100)
    """

    # Use defaults when score not available
    s10x = score_10x if score_10x is not None else Decimal("0")
    risk = score_risk if score_risk is not None else Decimal("50")  # assume medium risk
    momentum = score_momentum if score_momentum is not None else Decimal("0")
    undervaluation = score_undervaluation if score_undervaluation is not None else Decimal("0")

    reasoning = []
    composite = _compute_composite(s10x, risk, momentum, undervaluation, reasoning)

    # Calculate confidence based on how many scores we actually have
    available = sum([
        data_confidence_10x > 0,
        data_confidence_risk > 0,
        data_confidence_momentum > 0,
        data_confidence_undervaluation > 0,
    ])
    confidence = (Decimal(str(available)) / Decimal("4")).quantize(Decimal("0.01"))

    tier = _threshold_classify(composite, risk, reasoning)

    return TierClassification(
        tier=tier,
        label=TIER_LABELS[tier],
        description=TIER_DESCRIPTIONS[tier],
        confidence=confidence,
        reasoning=reasoning,
    )


def _compute_composite(
    s10x: Decimal,
    risk: Decimal,
    momentum: Decimal,
    undervaluation: Decimal,
    reasoning: list[str],
) -> Decimal:
    """Weighted composite score balancing upside vs risk.

    Upside signals (10X potential, momentum, undervaluation) vs risk.
    Higher composite = higher tier.
    """
    # Upside components: 10X potential is the strongest signal
    upside = (s10x * Decimal("0.45") +
              momentum * Decimal("0.25") +
              undervaluation * Decimal("0.30"))

    # Risk penalty: higher risk score = more risky = lower tier
    # Invert: risk contribution is (100 - risk), so low risk = high contribution
    risk_adj = (Decimal("100") - risk) * Decimal("0.35")

    # Composite: upside weighted + risk adjustment
    # Scale so max composite is ~100
    composite = (upside * Decimal("0.65") + risk_adj * Decimal("0.35")).quantize(Decimal("0.01"))

    if s10x > Decimal("70"):
        reasoning.append(f"Strong 10X potential score ({s10x})")
    if risk < Decimal("30"):
        reasoning.append(f"Low risk profile ({risk})")
    elif risk > Decimal("70"):
        reasoning.append(f"High risk profile ({risk})")
    if momentum > Decimal("60"):
        reasoning.append(f"Strong momentum ({momentum})")
    if undervaluation > Decimal("60"):
        reasoning.append(f"Potentially undervalued ({undervaluation})")

    return composite


def _threshold_classify(
    composite: Decimal,
    risk: Decimal,
    reasoning: list[str],
) -> RewardTier:
    """Apply thresholds to the composite score to determine tier.

    Thresholds calibrated so:
    - Moonshot: composite >= 65 AND risk >= 50 (high ceiling but volatile)
    - 10X Potential: composite >= 45 (strong upside signals)
    - 3X Growth: composite >= 25 (decent fundamentals)
    - 2X Safe: everything below 25 (conservative or unproven)
    """

    # High composite + high risk = Moonshot (volatile but high ceiling)
    if composite >= Decimal("60") and risk >= Decimal("50"):
        reasoning.append(f"High composite ({composite}) with high risk ({risk}) = Moonshot")
        return RewardTier.MOONSHOT

    # Strong composite = 10X Potential
    if composite >= Decimal("40"):
        reasoning.append(f"Strong composite score ({composite}) = 10X Potential")
        return RewardTier.POTENTIAL_10X

    # Decent composite = 3X Growth
    if composite >= Decimal("25"):
        reasoning.append(f"Moderate composite ({composite}) = 3X Growth")
        return RewardTier.GROWTH_3X

    # Everything else = 2X Safe
    reasoning.append(f"Conservative composite ({composite}) = 2X Safe")
    return RewardTier.SAFE_2X
