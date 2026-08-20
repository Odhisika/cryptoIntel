"""
Generic scoring framework.

Every score in this platform (10X Potential, Undervaluation, Momentum,
Risk) is built from named, weighted Factors. This module is the shared
machinery so that "explainable + versioned + honest about missing data"
is enforced once, not reimplemented per score.

Design decisions, and why:

- A Factor either has a value (0-100 normalized, with the raw input kept
  for the explanation) or is explicitly `insufficient_data`. There is no
  third option where a missing factor silently becomes 0 or 50 — both of
  those are fabrication (a 0 implies "this is bad," a 50 implies "this is
  average," and neither is true; the truth is "we don't know yet").
- When a factor is missing, its weight is NOT assigned to the asset as a
  penalty. Instead the score is computed over the renormalized weights of
  the *available* factors, and `data_confidence` (0-1, = available_weight
  / total_weight) is reported alongside the score so a low-confidence
  score can be visually distinguished from a well-supported one. This is
  what section 14 means by "how missing data is handled" — it has to be
  designed, not defaulted.
- Every score is tied to a `model_version` string. Changing weights means
  bumping the version, not mutating history in place (section 14: "The
  model must be versioned. Historical scores must remain associated with
  the model version that generated them.")
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class Factor:
    name: str
    weight: Decimal  # out of 100, per the model's defined weighting
    normalized_value: Optional[Decimal]  # 0-100, or None if insufficient_data
    raw_value: Optional[str]  # human-readable raw input, for the explanation
    insufficient_data: bool = False
    note: str = ""

    def __post_init__(self):
        if not self.insufficient_data:
            if self.normalized_value is None:
                raise ValueError(f"Factor '{self.name}' has no value but isn't marked insufficient_data")
            if not (Decimal("0") <= self.normalized_value <= Decimal("100")):
                raise ValueError(f"Factor '{self.name}' normalized_value out of 0-100 range")


@dataclass(frozen=True)
class ScoreResult:
    model_name: str
    model_version: str
    score: Decimal  # 0-100
    data_confidence: Decimal  # 0-1, fraction of total weight backed by real data
    factors: list[Factor]

    def top_contributors(self, n: int = 3) -> list[Factor]:
        scored = [f for f in self.factors if not f.insufficient_data]
        return sorted(scored, key=lambda f: f.weight * (f.normalized_value or 0), reverse=True)[:n]

    def missing_factors(self) -> list[Factor]:
        return [f for f in self.factors if f.insufficient_data]

    def as_explanation_dict(self) -> dict:
        """Structured explanation payload — this is what the AI research
        layer (Phase 9) is allowed to narrate. It must never add facts
        beyond what's in here."""
        return {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "score": float(self.score),
            "data_confidence": float(self.data_confidence),
            "factors": [
                {
                    "name": f.name,
                    "weight": float(f.weight),
                    "normalized_value": float(f.normalized_value) if f.normalized_value is not None else None,
                    "raw_value": f.raw_value,
                    "insufficient_data": f.insufficient_data,
                    "note": f.note,
                }
                for f in self.factors
            ],
        }


def compute_weighted_score(
    *, model_name: str, model_version: str, factors: list[Factor]
) -> ScoreResult:
    total_weight = sum(f.weight for f in factors)
    if total_weight <= 0:
        raise ValueError("Factor weights must sum to a positive number")

    available = [f for f in factors if not f.insufficient_data]
    available_weight = sum(f.weight for f in available)

    if available_weight == 0:
        # No data at all — report a 0 score with 0 confidence rather than
        # raising, so the caller can still store/display "unscored" state.
        return ScoreResult(
            model_name=model_name,
            model_version=model_version,
            score=Decimal("0"),
            data_confidence=Decimal("0"),
            factors=factors,
        )

    # Renormalize: each available factor's effective weight is scaled up
    # so the available weights sum to 100, then take the weighted average
    # of normalized values. This is the "missing data doesn't become a
    # penalty" rule described in the module docstring.
    weighted_sum = sum(f.weight * (f.normalized_value or Decimal("0")) for f in available)
    score = weighted_sum / available_weight

    data_confidence = available_weight / total_weight

    return ScoreResult(
        model_name=model_name,
        model_version=model_version,
        score=score.quantize(Decimal("0.01")),
        data_confidence=data_confidence.quantize(Decimal("0.0001")),
        factors=factors,
    )
