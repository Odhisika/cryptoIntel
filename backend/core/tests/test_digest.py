"""Tests for Feature 9 — the weekly email digest: data gathering (top
candidates, regime, score movers), plain/HTML rendering, subscriber delivery,
the Celery task, and the management command."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from core import digest
from core.digest import (
    build_digest_text, latest_regime, score_movers, send_weekly_digest, top_candidates,
)
from core.models import Asset, MarketRegimeSnapshot, MarketSnapshot, ScoreSnapshot, Subscription

pytestmark = pytest.mark.django_db

BASE = timezone.now()
_MKT_COUNTER = 0


def make_asset(symbol="SOL", active=True):
    return Asset.objects.create(symbol=symbol, name=symbol.upper(), is_active=active)


def make_market_snapshot(asset, observed_at=None):
    global _MKT_COUNTER
    # (asset, source, observed_at) is unique; always vary it per call so each
    # score gets its own MarketSnapshot regardless of the computed_at used.
    _MKT_COUNTER += 1
    observed_at = BASE + timedelta(microseconds=_MKT_COUNTER)
    return MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("1"), source="coingecko",
        observed_at=observed_at,
    )


def make_score(asset, model_name, score, at=None):
    market = make_market_snapshot(asset, observed_at=at or BASE)
    snap = ScoreSnapshot.objects.create(
        asset=asset, market_snapshot=market, model_name=model_name,
        model_version="v1.0", score=Decimal(str(score)),
        data_confidence=Decimal("1.0"),
    )
    if at is not None:
        # computed_at is auto_now_add; force it for predictable ordering.
        ScoreSnapshot.objects.filter(pk=snap.pk).update(computed_at=at)
    return snap


def make_full_scores(asset, score, at):
    for m in ["10x_potential", "undervaluation", "momentum", "risk"]:
        make_score(asset, m, score, at=at)


def make_regime(regime="bullish", btc=30000.0, at=None):
    return MarketRegimeSnapshot.objects.create(
        btc_price_usd=Decimal(str(btc)), eth_price_usd=Decimal("2000"),
        btc_change_7d_pct=Decimal("3.5"), btc_above_50dma=True,
        btc_50dma_value=Decimal("29000"), regime=regime,
        regime_confidence=Decimal("0.8"), source="binance",
        observed_at=at or BASE,
    )


def make_active_subscriber(email="user@example.com", user_id="u1"):
    return Subscription.objects.create(
        user_id=user_id, email=email, status=Subscription.Status.ACTIVE,
        expires_at=BASE + timedelta(days=30), plan="monthly",
    )


class TestTopCandidates:
    def test_ranks_by_10x_score_and_includes_tier(self):
        a = make_asset("AAA")
        b = make_asset("BBB")
        make_score(a, "10x_potential", 90, at=BASE)
        make_score(b, "10x_potential", 40, at=BASE)
        for m in ["undervaluation", "momentum", "risk"]:
            make_score(a, m, 80, at=BASE)
        rows = top_candidates()
        assert [r["symbol"] for r in rows] == ["AAA", "BBB"]
        assert rows[0]["score_10x"] == Decimal("90.00")
        assert rows[0]["tier"]  # a tier label is present

    def test_excludes_unscored_and_inactive(self):
        make_asset("NONE")
        make_score(make_asset("ACT", active=True), "10x_potential", 50, at=BASE)
        inactive = make_asset("GONE", active=False)
        make_score(inactive, "10x_potential", 99, at=BASE)
        rows = top_candidates()
        assert [r["symbol"] for r in rows] == ["ACT"]
        new_lim = top_candidates(1)
        assert len(new_lim) == 1

    def test_limits_n(self):
        for sym in ["A", "B", "C"]:
            make_score(make_asset(sym), "10x_potential", 50, at=BASE)
        assert len(top_candidates(2)) == 2


class TestRegime:
    def test_returns_latest_regime(self):
        make_regime(regime="bearish", at=BASE - timedelta(days=1))
        make_regime(regime="bullish", at=BASE)
        regime = latest_regime()
        assert regime.regime == "bullish"

    def test_no_regime_returns_none(self):
        assert latest_regime() is None


class TestScoreMovers:
    def test_splits_gainers_and_decliners(self):
        up = make_asset("UP")
        down = make_asset("DOWN")
        make_score(up, "10x_potential", 50, at=BASE - timedelta(days=1))
        make_score(up, "10x_potential", 90, at=BASE)   # +40
        make_score(down, "10x_potential", 90, at=BASE - timedelta(days=1))
        make_score(down, "10x_potential", 60, at=BASE)  # -30
        gainers, decliners = score_movers()
        assert [g["symbol"] for g in gainers] == ["UP"]
        assert [g["delta"] for g in gainers] == [Decimal("40.00")]
        assert [d["symbol"] for d in decliners] == ["DOWN"]
        assert decliners[0]["delta"] == Decimal("-30.00")

    def test_sorted_by_absolute_delta(self):
        big = make_asset("BIG")
        small = make_asset("SMALL")
        make_score(big, "10x_potential", 10, at=BASE - timedelta(days=1))
        make_score(big, "10x_potential", 60, at=BASE)  # +50
        make_score(small, "10x_potential", 50, at=BASE - timedelta(days=1))
        make_score(small, "10x_potential", 56, at=BASE)  # +6
        gainers, _ = score_movers(limit=10)
        assert [g["symbol"] for g in gainers] == ["BIG", "SMALL"]

    def test_ignores_assets_with_single_snapshot(self):
        make_score(make_asset("ONLYONE"), "10x_potential", 80, at=BASE)
        gainers, decliners = score_movers()
        assert gainers == []
        assert decliners == []

    def test_zero_delta_not_a_mover(self):
        a = make_asset("FLAT")
        make_score(a, "10x_potential", 80, at=BASE - timedelta(days=1))
        make_score(a, "10x_potential", 80, at=BASE)
        gainers, decliners = score_movers()
        assert gainers == [] and decliners == []

    def test_tier_upgrade_detected(self):
        a = make_asset("RISE")
        make_full_scores(a, 20, at=BASE - timedelta(days=1))  # low → 2x_safe
        make_full_scores(a, 90, at=BASE)                      # high → 10x_potential+
        gainers, _ = score_movers()
        assert len(gainers) == 1
        assert gainers[0]["tier_change"] is not None
        assert gainers[0]["tier_change"]["kind"] == "Upgrade"


class TestBuildDigestText:
    def test_plain_sections_present(self):
        text = build_digest_text(
            top=top_candidates(),
            regime=make_regime(),
            gainers=[], decliners=[],
            as_html=False,
        )
        assert "TOP 10X CANDIDATES" in text
        assert "MARKET REGIME" in text
        assert "UPGRADES" in text
        assert "DOWNGRADES" in text

    def test_html_wraps_sections(self):
        text = build_digest_text(
            top=[{"symbol": "SOL", "name": "Solana", "score_10x": Decimal("90"), "tier": "10x_potential"}],
            regime=make_regime(),
            gainers=[], decliners=[],
            as_html=True,
        )
        assert "<h2>🏆 Top 10X Candidates</h2>" in text
        assert "<b>90.0</b>" in text

    def test_graceful_when_no_data(self):
        text = build_digest_text(as_html=False)
        assert "No scored tokens yet" in text
        assert "No regime data" in text


class TestSendDigest:
    def test_emails_each_recipient(self):
        make_active_subscriber("one@example.com", user_id="u1")
        make_active_subscriber("two@example.com", user_id="u2")
        Asset.objects.create(symbol="SOL", name="Solana", is_active=True)
        with patch("core.digest.send_mail") as send:
            result = send_weekly_digest()
        assert result["recipients"] == 2
        assert result["sent"] == 2
        assert send.call_count == 2

    def test_skips_empty_email_and_expired(self):
        make_active_subscriber("ok@example.com")
        Subscription.objects.create(
            user_id="u2", email="", status=Subscription.Status.ACTIVE,
            expires_at=BASE + timedelta(days=30),
        )
        Subscription.objects.create(
            user_id="u3", email="expired@example.com", status=Subscription.Status.ACTIVE,
            expires_at=BASE - timedelta(days=1),
        )
        with patch("core.digest.send_mail") as send:
            result = send_weekly_digest()
        assert result["recipients"] == 1
        assert send.call_count == 1

    def test_failure_isolated_per_recipient(self):
        make_active_subscriber("a@example.com", user_id="ua")
        make_active_subscriber("b@example.com", user_id="ub")
        with patch("core.digest.send_mail",
                   side_effect=[RuntimeError("smtp down"), None]) as send:
            result = send_weekly_digest()
        assert result["recipients"] == 2
        assert result["sent"] == 1
        assert result["failed"] == 1

    def test_no_subscribers_is_noop(self):
        result = send_weekly_digest()
        assert result["recipients"] == 0
        assert result["sent"] == 0


class TestTask:
    def test_task_calls_send_weekly_digest(self):
        from core.tasks.digest import send_weekly_email_digest
        with patch("core.tasks.digest.send_weekly_digest",
                   return_value={"recipients": 3}) as m:
            result = send_weekly_email_digest()
        m.assert_called_once_with()


class TestCommand:
    def test_dry_run_prints_digest(self):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command("send_weekly_digest", dry_run=True, stdout=out)
        assert "TOP 10X CANDIDATES" in out.getvalue()

    def test_single_email(self):
        from django.core.management import call_command
        from io import StringIO
        with patch("django.core.mail.send_mail") as send:
            call_command("send_weekly_digest", email="you@example.com", stdout=StringIO())
        send.assert_called_once()
        assert send.call_args[1]["recipient_list"] == ["you@example.com"]