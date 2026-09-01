"""Tests for historical score accuracy / backtesting (Feature 3)."""

from datetime import timedelta
from decimal import Decimal

import jwt
import pytest
from django.conf import settings
from django.utils import timezone

from core.backtest import (
    build_backtest_report,
    forward_return,
    performance_metrics,
    build_headline,
)
from core.models import Asset, MarketSnapshot, ScoreSnapshot, Subscription
from core.scoring.tiers import RewardTier

pytestmark = pytest.mark.django_db

NOW = timezone.now()
USER_ID = "site-user-42"


def make_asset(symbol="SOL"):
    return Asset.objects.create(symbol=symbol, name=symbol.upper(), is_active=True)


def make_market_snapshot(asset, price, observed_at):
    return MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal(str(price)),
        source="coingecko", observed_at=observed_at,
    )


def make_scores(asset, baseline, *, s10x=90, momentum=80, underval=70, risk_score=20, model_version="v1.0"):
    """Create all 4 ScoreSnapshots at the baseline's time for tier classification."""
    return {
        "10x_potential": ScoreSnapshot.objects.create(
            asset=asset, market_snapshot=baseline, model_name="10x_potential",
            model_version=model_version, score=Decimal(str(s10x)),
            data_confidence=Decimal("1.0"), computed_at=baseline.observed_at,
        ),
        "momentum": ScoreSnapshot.objects.create(
            asset=asset, market_snapshot=baseline, model_name="momentum",
            model_version=model_version, score=Decimal(str(momentum)),
            data_confidence=Decimal("1.0"), computed_at=baseline.observed_at,
        ),
        "undervaluation": ScoreSnapshot.objects.create(
            asset=asset, market_snapshot=baseline, model_name="undervaluation",
            model_version=model_version, score=Decimal(str(underval)),
            data_confidence=Decimal("1.0"), computed_at=baseline.observed_at,
        ),
        "risk": ScoreSnapshot.objects.create(
            asset=asset, market_snapshot=baseline, model_name="risk",
            model_version=model_version, score=Decimal(str(risk_score)),
            data_confidence=Decimal("1.0"), computed_at=baseline.observed_at,
        ),
    }


class TestForwardReturn:
    def test_positive_return_when_price_rises(self):
        asset = make_asset()
        baseline = make_market_snapshot(asset, 100, NOW - timedelta(days=40))
        make_market_snapshot(asset, 130, NOW - timedelta(days=10))  # +30 days

        ret = forward_return(asset, baseline, 30)

        assert ret == Decimal("0.3000")

    def test_negative_return_when_price_falls(self):
        asset = make_asset()
        baseline = make_market_snapshot(asset, 100, NOW - timedelta(days=40))
        make_market_snapshot(asset, 80, NOW - timedelta(days=10))

        ret = forward_return(asset, baseline, 30)

        assert ret == Decimal("-0.2000")

    def test_none_when_no_forward_snapshot(self):
        asset = make_asset()
        baseline = make_market_snapshot(asset, 100, NOW - timedelta(days=40))
        # no future snapshots

        ret = forward_return(asset, baseline, 30)

        assert ret is None

    def test_none_when_baseline_price_zero(self):
        asset = make_asset()
        baseline = make_market_snapshot(asset, 0, NOW - timedelta(days=40))
        make_market_snapshot(asset, 130, NOW - timedelta(days=10))

        assert forward_return(asset, baseline, 30) is None


class TestPerformanceMetrics:
    def test_empty_returns_yield_none_metrics(self):
        metrics = performance_metrics([])
        assert metrics == {"win_rate": None, "avg_return": None, "sharpe": None, "count": 0}

    def test_all_wins(self):
        metrics = performance_metrics([Decimal("0.1"), Decimal("0.2")], horizon_days=30)
        assert metrics["win_rate"] == Decimal("1.0000")
        assert metrics["avg_return"] == Decimal("0.1500")
        assert metrics["count"] == 2

    def test_win_rate_is_share_of_positive_returns(self):
        metrics = performance_metrics([Decimal("0.1"), Decimal("-0.1"), Decimal("0.0")], horizon_days=30)
        assert metrics["win_rate"] == Decimal("0.3333")
        assert metrics["avg_return"] == Decimal("0.0000")

    def test_zero_std_means_none_sharpe(self):
        metrics = performance_metrics([Decimal("0.05"), Decimal("0.05")], horizon_days=30)
        assert metrics["sharpe"] is None


class TestBuildBacktestReport:
    def test_reports_per_tier_and_summary(self):
        asset = make_asset()
        baseline = make_market_snapshot(asset, 100, NOW - timedelta(days=40))
        make_scores(asset, baseline)
        make_market_snapshot(asset, 130, NOW - timedelta(days=10))

        report = build_backtest_report(now=NOW)

        h30 = report["horizons"]["30"]
        assert h30["scores_evaluated"] == 1
        assert h30["summary"]["count"] == 1
        assert h30["summary"]["avg_return"] == Decimal("0.3000")
        # A high-10x-score asset should be classified POTENTIAL_10X.
        assert h30["tiers"][RewardTier.POTENTIAL_10X.value]["count"] == 1

    def test_ignores_scores_without_full_forward_window(self):
        asset = make_asset()
        baseline = make_market_snapshot(asset, 100, NOW - timedelta(days=10))  # too recent for 30d
        make_scores(asset, baseline)
        make_market_snapshot(asset, 130, NOW + timedelta(days=20))

        report = build_backtest_report(now=NOW)

        assert report["horizons"]["30"]["scores_evaluated"] == 0

    def test_model_version_filter(self):
        asset = make_asset()
        baseline = make_market_snapshot(asset, 100, NOW - timedelta(days=40))
        make_scores(asset, baseline, model_version="v1.0")
        # Second asset scored with a different model version.
        asset2 = make_asset("ETH")
        baseline2 = make_market_snapshot(asset2, 100, NOW - timedelta(days=40))
        make_scores(asset2, baseline2, model_version="v2.0")
        make_market_snapshot(asset, 130, NOW - timedelta(days=10))
        make_market_snapshot(asset2, 120, NOW - timedelta(days=10))

        report = build_backtest_report(model_version="v1.0", now=NOW)

        assert report["horizons"]["30"]["summary"]["count"] == 1

    def test_headline_falls_back_without_data(self):
        report = build_backtest_report(now=NOW)
        assert "Not enough historical data" in report["headline"]

    def test_headline_has_metrics_with_data(self):
        asset = make_asset()
        baseline = make_market_snapshot(asset, 100, NOW - timedelta(days=100))
        make_scores(asset, baseline)
        make_market_snapshot(asset, 130, NOW - timedelta(days=10))

        report = build_backtest_report(now=NOW)

        assert "returned on average" in report["headline"]
        assert "% win rate" in report["headline"]

    def test_build_headline_empty(self):
        assert "Not enough historical data" in build_headline({"horizons": {"90": {"summary": {"count": 0}}}})


class TestHeadline:
    def test_headline_uses_avg_return_and_win_rate(self):
        headline = build_headline(
            {"horizons": {"90": {"summary": {"count": 10, "avg_return": Decimal("0.1234"), "win_rate": Decimal("0.71")}}}},
            horizons=[30, 60, 90],
        )
        assert "12.3%" in headline
        assert "71%" in headline
        assert "10 score observations" in headline


class TestBacktestAPI:
    def _auth_headers(self, user_id=USER_ID):
        token = jwt.encode(
            {"user_id": user_id, "email": "u@example.com"},
            settings.JWT_SIGNING_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def test_requires_subscription(self, client):
        resp = client.get("/api/v1/backtest/", **self._auth_headers("no-sub"))
        assert resp.status_code == 403

    def test_returns_report_with_str_decimals(self, client):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=NOW + timedelta(days=30))
        asset = make_asset()
        baseline = make_market_snapshot(asset, 100, NOW - timedelta(days=100))
        make_scores(asset, baseline)
        make_market_snapshot(asset, 130, NOW - timedelta(days=10))

        resp = client.get("/api/v1/backtest/", **self._auth_headers())

        assert resp.status_code == 200
        body = resp.json()
        assert "headline" in body
        assert body["model_version"] is None
        # 90-day horizon should have our one observation, avg return as a string.
        h90 = body["horizons"]["90"]["summary"]
        assert h90["count"] == 1
        assert h90["avg_return"] == "0.3000"
        assert h90["win_rate"] == "1.0000"

    def test_model_version_param_forwarded(self, client):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=NOW + timedelta(days=30))
        asset = make_asset()
        baseline = make_market_snapshot(asset, 100, NOW - timedelta(days=100))
        make_scores(asset, baseline)
        make_market_snapshot(asset, 130, NOW - timedelta(days=10))

        resp = client.get("/api/v1/backtest/?model_version=v1.0", **self._auth_headers())

        assert resp.status_code == 200
        body = resp.json()
        assert body["model_version"] == "v1.0"
        assert body["horizons"]["90"]["summary"]["count"] == 1
