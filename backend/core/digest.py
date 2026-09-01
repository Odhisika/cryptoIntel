"""
Weekly Email Digest composition (Roadmap Tier 3, Feature 9).

Three sections, all derived from real, point-in-time data (no look-ahead):
  1. Top 10X candidates  — the highest latest 10x_potential scores (with tier).
  2. Market regime summary — the most recent MarketRegimeSnapshot (regime,
     BTC/ETH price + 7d change, BTC vs 50DMA).
  3. Biggest score movers  — per-asset delta between the latest and the
     PREVIOUS 10x_potential ScoreSnapshot, split into gainers (upgrades) and
     decliners (downgrades), with the tier change noted when the reward tier
     moved between the latest and previous scored states.

Delivery: build_digest_text(...) is PURE and returns the message body so it is
trivially testable. send_weekly_digest(...) renders and emails it to every
active subscriber (an ACTIVE Subscription with a non-empty email). The Celery
task wraps that; a management command is also provided.

Honesty convention (project-wide): if no scores/regime exist yet, the digest
says so gracefully instead of printing fabricated zeros.
"""

import logging
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from core.models import MarketRegimeSnapshot, ScoreSnapshot, Asset, Subscription
from core.scoring.tiers import classify_tier, TIER_LABELS

logger = logging.getLogger(__name__)

DIGEST_TITLE = "Crypto Intel — Weekly Digest"
DIGEST_SENDER = getattr(settings, "DEFAULT_FROM_EMAIL", "Crypto Intel <digest@example.com>")
NUM_TOP = 10
NUM_MOVERS = 5

TIER_RANK = {
    "2x_safe": 0,
    "3x_growth": 1,
    "10x_potential": 2,
    "moonshot": 3,
}


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------

def latest_regime():
    return (
        MarketRegimeSnapshot.objects.order_by("-observed_at").first()
    )


def top_candidates(n=NUM_TOP):
    """Top `n` active assets by latest 10x_potential score, with tier."""
    rows = []
    for asset in Asset.objects.filter(is_active=True):
        score = _latest_score(asset, "10x_potential")
        if score is None:
            continue
        tier = _tier_label(_current_scores(asset))
        rows.append({
            "symbol": asset.symbol,
            "name": asset.name,
            "score_10x": score.score,
            "confidence": score.data_confidence,
            "tier": tier,
        })
    rows.sort(key=lambda r: r["score_10x"], reverse=True)
    return rows[:n]


def score_movers(limit=NUM_MOVERS):
    """Assets whose latest 10x_potential score moved from the previous one,
    split into gainers and decliners by delta magnitude, with tier change.

    Returns (gainers, decliners) — each a list of dicts sorted by |delta| desc.
    A tier change is recorded when the latest scored state classifies into a
    different reward tier than the previous scored state."""
    movers = []
    for asset in Asset.objects.filter(is_active=True):
        current = _latest_score(asset, "10x_potential")
        previous = _previous_score(asset, "10x_potential")
        if current is None or previous is None:
            continue
        delta = Decimal(current.score) - Decimal(previous.score)
        if delta == 0:
            continue

        tier_before = _tier_label(_previous_scores(asset))
        tier_after = _tier_label(_current_scores(asset))
        tier_change = _tier_delta(tier_before, tier_after)

        movers.append({
            "symbol": asset.symbol,
            "name": asset.name,
            "before": previous.score,
            "after": current.score,
            "delta": delta,
            "tier_before": tier_before,
            "tier_after": tier_after,
            "tier_change": tier_change,
        })

    movers.sort(key=lambda r: abs(r["delta"]), reverse=True)
    gainers = [m for m in movers if m["delta"] > 0][:limit]
    decliners = [m for m in movers if m["delta"] < 0][:limit]
    return gainers, decliners


# ---------------------------------------------------------------------------
# Rendering (pure)
# ---------------------------------------------------------------------------

def build_digest_text(top=None, regime=None, gainers=None, decliners=None, as_html=False) -> str:
    top = _ensure(top, top_candidates)
    regime = _ensure(regime, latest_regime)
    if gainers is None or decliners is None:
        g, d = score_movers()
        gainers = gainers if gainers is not None else g
        decliners = decliners if decliners is not None else d

    lines = []
    if as_html:
        lines = ["<h1>🤖 Crypto Intel — Weekly Digest</h1>"]

    lines += _render_top(top, as_html)
    lines += _render_regime(regime, as_html)
    lines += _render_movers("Upgrades", gainers, "moved up", as_html)
    lines += _render_movers("Downgrades", decliners, "moved down", as_html)

    if as_html:
        return "<br>".join(lines)
    return "\n".join(lines)


def _render_top(top, as_html) -> list:
    out = [("<h2>🏆 Top 10X Candidates</h2>" if as_html else "🏆 TOP 10X CANDIDATES")]
    if not top:
        out.append(_dim("No scored tokens yet. Run a scoring pass and check back next week.", as_html))
        return out
    for i, r in enumerate(top, 1):
        line = f"{i}. {r['symbol'].upper()} ({r['name']}) — 10X <b>{_f(r['score_10x'])}</b>" if as_html else \
               f"{i}. {r['symbol'].upper()} ({r['name']}) — 10X {_f(r['score_10x'])}"
        if r.get("tier"):
            line += f" · {r['tier']}"
        out.append(line)
    return out


def _render_regime(regime, as_html) -> list:
    out = [("<h2>📊 Market Regime</h2>" if as_html else "📊 MARKET REGIME")]
    if regime is None:
        out.append(_dim("No regime data available yet.", as_html))
        return out

    label = regime.get_regime_display()
    out.append(f"Regime: <b>{label}</b> (confidence {_f(regime.regime_confidence)})" if as_html
               else f"Regime: {label} (confidence {_f(regime.regime_confidence)})")
    out.append(f"BTC ${_f(regime.btc_price_usd)} ({_signed(regime.btc_change_7d_pct)} 7d)")
    if regime.btc_above_50dma is not None:
        dma = "above" if regime.btc_above_50dma else "below"
        out.append(f"BTC {dma} its 50-day MA (${_f(regime.btc_50dma_value)})")
    return out


def _render_movers(header, items, verb, as_html) -> list:
    out = [f"<h2>📈 {header}</h2>" if as_html else f"📈 {header.upper()}"]
    if not items:
        out.append(_dim("None this week.", as_html))
        return out
    for m in items:
        line = f"{m['symbol'].upper()} ({m['name']}) — 10X <b>{_f(m['before'])}</b> ↔ <b>{_f(m['after'])}</b> ({_signed(m['delta'])})" if as_html else \
               f"{m['symbol'].upper()} ({m['name']}) — 10X {_f(m['before'])} → {_f(m['after'])} ({_signed(m['delta'])})"
        if m.get("tier_change"):
            tc = m["tier_change"]
            line += f" · {tc['kind']} to {tc['label']}"
        out.append(line)
    return out


def _tier_delta(before, after):
    if not before or not after:
        return None
    a = TIER_RANK.get(before)
    b = TIER_RANK.get(after)
    if a is None or b is None or a == b:
        return None
    return {"kind": "Upgrade" if b > a else "Downgrade", "label": after}


def _dim(text, as_html) -> str:
    return f"<i>{text}</i>" if as_html else text


def _ensure(value, default_fn):
    return value if value is not None else default_fn()


# ---------------------------------------------------------------------------
# Score helpers (append-only ScoreSnapshot history)
# ---------------------------------------------------------------------------

def _latest_score(asset, model_name):
    return (
        ScoreSnapshot.objects
        .filter(asset=asset, model_name=model_name)
        .order_by("-computed_at")
        .first()
    )


def _previous_score(asset, model_name):
    snaps = list(
        ScoreSnapshot.objects
        .filter(asset=asset, model_name=model_name)
        .order_by("-computed_at")[:2]
    )
    return snaps[1] if len(snaps) > 1 else None


def _current_scores(asset):
    out = {}
    for m in ["10x_potential", "undervaluation", "momentum", "risk"]:
        snap = _latest_score(asset, m)
        if snap:
            out[m] = snap
    return out


def _previous_scores(asset):
    out = {}
    for m in ["10x_potential", "undervaluation", "momentum", "risk"]:
        snap = _previous_score(asset, m)
        if snap:
            out[m] = snap
    return out


def _tier_label(scores):
    if not scores:
        return None
    result = classify_tier(
        score_10x=scores.get("10x_potential").score if "10x_potential" in scores else None,
        score_risk=scores.get("risk").score if "risk" in scores else None,
        score_momentum=scores.get("momentum").score if "momentum" in scores else None,
        score_undervaluation=scores.get("undervaluation").score if "undervaluation" in scores else None,
        data_confidence_10x=scores.get("10x_potential").data_confidence if "10x_potential" in scores else 0,
        data_confidence_risk=scores.get("risk").data_confidence if "risk" in scores else 0,
        data_confidence_momentum=scores.get("momentum").data_confidence if "momentum" in scores else 0,
        data_confidence_undervaluation=scores.get("undervaluation").data_confidence if "undervaluation" in scores else 0,
    )
    return result.tier.value


def _f(val, decimals=1):
    if val is None:
        return "-"
    return f"{Decimal(str(val)):.{decimals}f}"


def _signed(val):
    if val is None:
        return "-"
    d = Decimal(str(val))
    return f"{d:+.2f}%"


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def subscriber_emails():
    """Active subscribers with an email address (ACTIVE + non-empty email +
    not yet expired)."""
    now = timezone.now()
    return (
        Subscription.objects
        .filter(status=Subscription.Status.ACTIVE, expires_at__gt=now)
        .exclude(email="")
        .values_list("email", flat=True)
        .distinct()
    )


def send_weekly_digest(top=None, regime=None) -> dict:
    """Email the digest to every active subscriber. Returns per-recipient
    delivery counts; a transport failure for one recipient never aborts the
    rest. No subscribers / no data yields graceful no-ops."""
    top_ = _ensure(top, top_candidates)
    regime_ = _ensure(regime, latest_regime)
    gainers, decliners = score_movers()

    text = build_digest_text(top_, regime_, gainers, decliners, as_html=False)
    html = build_digest_text(top_, regime_, gainers, decliners, as_html=True)

    emails = list(subscriber_emails())
    sent, failed = 0, 0
    for email in emails:
        try:
            send_mail(
                subject=DIGEST_TITLE,
                message=text,
                from_email=DIGEST_SENDER,
                recipient_list=[email],
                html_message=html,
                fail_silently=False,
            )
            sent += 1
        except Exception:
            logger.exception("Weekly digest delivery failed for %s", email)
            failed += 1

    return {
        "recipients": len(emails),
        "sent": sent,
        "failed": failed,
        "top_count": len(top_),
        "regime": regime_.regime if regime_ else None,
        "movers_up": len(gainers),
        "movers_down": len(decliners),
    }