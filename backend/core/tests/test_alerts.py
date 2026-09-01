"""Tests for Real-Time Alerts (Feature 2): evaluation logic, the Celery
task, notification delivery, and the alert API endpoints."""

import json
from datetime import timedelta
from decimal import Decimal

import jwt
import pytest
from django.conf import settings
from django.utils import timezone

from core.alerts import (
    get_metric_value,
    operator_label,
    threshold_crossed,
)
from core.models import (
    AlertEvent,
    AlertRule,
    Asset,
    MarketSnapshot,
    ScoreSnapshot,
    Subscription,
)
from core.notifications import render_alert_message
from core.tasks.alerts import process_alerts

pytestmark = pytest.mark.django_db

USER_ID = "site-user-42"


def make_asset(symbol="SOL"):
    return Asset.objects.create(symbol=symbol, name=symbol.upper(), is_active=True)


def make_market_snapshot(asset, price, observed_at, market_cap=None, volume=None):
    return MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal(str(price)),
        market_cap_usd=Decimal(str(market_cap)) if market_cap is not None else None,
        volume_24h_usd=Decimal(str(volume)) if volume is not None else None,
        source="coingecko", observed_at=observed_at,
    )


def make_score_snapshot(asset, model_name, score, observed_at):
    snap = make_market_snapshot(asset, 1.0, observed_at)
    return ScoreSnapshot.objects.create(
        asset=asset, market_snapshot=snap, model_name=model_name,
        model_version="v1.0", score=Decimal(str(score)),
        data_confidence=Decimal("1.0"),
    )


def make_rule(asset=None, metric="score_10x", operator="gt", threshold=80,
              channel="email", email="user@example.com", name="test rule", **kwargs):
    return AlertRule.objects.create(
        user_id=USER_ID, email=email, name=name,
        asset=asset, metric=metric, operator=operator, threshold=Decimal(str(threshold)),
        channel=channel, **kwargs,
    )


# ---------------------------------------------------------------------------
# Rule model
# ---------------------------------------------------------------------------

class TestAlertRuleModel:
    def test_cooldown_defaults_to_false(self):
        rule = make_rule()
        assert rule.in_cooldown is False

    def test_cooldown_is_true_inside_window(self):
        rule = make_rule(cooldown_minutes=60)
        rule.last_fired_at = timezone.now()  # just fired
        rule.save()
        assert rule.in_cooldown is True

    def test_cooldown_expires(self):
        rule = make_rule(cooldown_minutes=60)
        rule.last_fired_at = timezone.now() - timedelta(minutes=61)
        rule.save()
        assert rule.in_cooldown is False

    def test_str(self):
        asset = make_asset()
        rule = make_rule(asset=asset)
        assert "SOL" in str(rule)
        assert "score_10x" in str(rule)


# ---------------------------------------------------------------------------
# Metric evaluation
# ---------------------------------------------------------------------------

class TestMetricEvaluation:
    def test_score_metric_reads_latest_score(self):
        asset = make_asset()
        make_score_snapshot(asset, "10x_potential", 85, timezone.now())
        assert get_metric_value(asset, "score_10x") == Decimal("85")

    def test_score_metric_uses_most_recent(self):
        asset = make_asset()
        make_score_snapshot(asset, "10x_potential", 40, timezone.now() - timedelta(hours=2))
        make_score_snapshot(asset, "10x_potential", 90, timezone.now())
        assert get_metric_value(asset, "score_10x") == Decimal("90")

    def test_score_metric_none_when_no_scores(self):
        asset = make_asset()
        assert get_metric_value(asset, "score_10x") is None

    def test_market_cap_pct_change(self):
        asset = make_asset()
        old = timezone.now() - timedelta(hours=25)
        make_market_snapshot(asset, 1.0, old, market_cap=100)
        make_market_snapshot(asset, 1.0, timezone.now(), market_cap=150)
        value = get_metric_value(asset, "market_cap_pct_change_24h")
        assert value == Decimal("50.0000")

    def test_volume_pct_change(self):
        asset = make_asset()
        old = timezone.now() - timedelta(hours=25)
        make_market_snapshot(asset, 1.0, old, volume=200)
        make_market_snapshot(asset, 1.0, timezone.now(), volume=100)
        assert get_metric_value(asset, "volume_pct_change_24h") == Decimal("-50.0000")

    def test_pct_change_returns_none_with_single_snapshot(self):
        asset = make_asset()
        make_market_snapshot(asset, 1.0, timezone.now(), market_cap=100)
        assert get_metric_value(asset, "market_cap_pct_change_24h") is None

    def test_pct_change_returns_none_on_zero_previous(self):
        asset = make_asset()
        old = timezone.now() - timedelta(hours=25)
        make_market_snapshot(asset, 1.0, old, market_cap=0)
        make_market_snapshot(asset, 1.0, timezone.now(), market_cap=150)
        assert get_metric_value(asset, "market_cap_pct_change_24h") is None

    def test_unknown_metric_returns_none(self):
        asset = make_asset()
        assert get_metric_value(asset, "bogus_metric") is None


# ---------------------------------------------------------------------------
# Threshold crossing
# ---------------------------------------------------------------------------

class TestThresholdCrossed:
    def test_gt(self):
        assert threshold_crossed("gt", Decimal("81"), Decimal("80")) is True
        assert threshold_crossed("gt", Decimal("80"), Decimal("80")) is False

    def test_gte(self):
        assert threshold_crossed("gte", Decimal("80"), Decimal("80")) is True
        assert threshold_crossed("gte", Decimal("79"), Decimal("80")) is False

    def test_lt(self):
        assert threshold_crossed("lt", Decimal("79"), Decimal("80")) is True
        assert threshold_crossed("lt", Decimal("80"), Decimal("80")) is False

    def test_lte(self):
        assert threshold_crossed("lte", Decimal("80"), Decimal("80")) is True
        assert threshold_crossed("lte", Decimal("81"), Decimal("80")) is False

    def test_invalid_operator_never_crosses(self):
        assert threshold_crossed("bogus", Decimal("90"), Decimal("80")) is False


# ---------------------------------------------------------------------------
# Celery task (evaluation + dispatch)
# ---------------------------------------------------------------------------

class TestProcessAlerts:
    def test_fires_email_when_score_crosses_threshold(self):
        asset = make_asset()
        make_score_snapshot(asset, "10x_potential", 90, timezone.now())
        rule = make_rule(asset=asset, metric="score_10x", operator="gt", threshold=80)

        result = process_alerts()

        assert result["fired"] == 1
        event = AlertEvent.objects.get(rule=rule)
        assert event.status == AlertEvent.Status.SENT
        assert event.channels == ["email"]
        assert event.observed_value == Decimal("90")
        rule.refresh_from_db()
        assert rule.last_fired_at is not None

    def test_does_not_fire_below_threshold(self):
        asset = make_asset()
        make_score_snapshot(asset, "10x_potential", 50, timezone.now())
        rule = make_rule(asset=asset, metric="score_10x", operator="gt", threshold=80)

        result = process_alerts()

        assert result["fired"] == 0
        assert not AlertEvent.objects.filter(rule=rule).exists()

    def test_respects_cooldown(self):
        asset = make_asset()
        make_score_snapshot(asset, "10x_potential", 90, timezone.now())
        rule = make_rule(asset=asset, metric="score_10x", operator="gt", threshold=80)

        process_alerts()
        process_alerts()  # second pass within cooldown

        assert AlertEvent.objects.filter(rule=rule).count() == 1

    def test_cooldown_is_per_asset(self):
        asset_a = make_asset("AAA")
        asset_b = make_asset("BBB")
        make_score_snapshot(asset_a, "10x_potential", 90, timezone.now())
        make_score_snapshot(asset_b, "10x_potential", 95, timezone.now())
        rule = make_rule(asset=None, metric="score_10x", operator="gt", threshold=80, cooldown_minutes=60)

        # Fires for both assets in the first pass; within cooldown on the second.
        process_alerts()
        process_alerts()

        assert AlertEvent.objects.filter(rule=rule).count() == 2

    def test_skips_inactive_rules(self):
        asset = make_asset()
        make_score_snapshot(asset, "10x_potential", 90, timezone.now())
        make_rule(asset=asset, metric="score_10x", operator="gt", threshold=80, is_active=False)

        result = process_alerts()

        assert result["fired"] == 0
        assert AlertEvent.objects.count() == 0

    def test_global_rule_checks_all_active_assets(self):
        asset_a = make_asset("AAA")
        asset_b = make_asset("BBB")
        make_market_snapshot(asset_a, 1.0, timezone.now())
        make_market_snapshot(asset_b, 1.0, timezone.now())
        make_score_snapshot(asset_a, "10x_potential", 90, timezone.now())
        make_score_snapshot(asset_b, "10x_potential", 95, timezone.now())
        rule = make_rule(asset=None, metric="score_10x", operator="gt", threshold=80)

        result = process_alerts()

        assert result["fired"] == 2
        assert set(AlertEvent.objects.filter(rule=rule).values_list("asset_id", flat=True)) == {
            asset_a.id, asset_b.id,
        }

    def test_recent_asset_scope(self):
        asset_a = make_asset("AAA")
        asset_b = make_asset("BBB")
        make_score_snapshot(asset_a, "10x_potential", 90, timezone.now())
        make_score_snapshot(asset_b, "10x_potential", 90, timezone.now())
        rule = make_rule(asset=None, metric="score_10x", operator="gt", threshold=80)

        result = process_alerts(recent_asset_ids=[asset_a.id])

        assert result["fired"] == 1
        assert AlertEvent.objects.count() == 1
        assert AlertEvent.objects.get().asset_id == asset_a.id

    def test_rule_id_scope(self):
        asset = make_asset()
        make_score_snapshot(asset, "10x_potential", 90, timezone.now())
        rule_a = make_rule(asset=asset, metric="score_10x", operator="gt", threshold=80, name="a")
        rule_b = make_rule(asset=asset, metric="score_10x", operator="gt", threshold=80, name="b")

        result = process_alerts(rule_ids=[rule_a.id])

        assert result["fired"] == 1
        assert AlertEvent.objects.filter(rule=rule_a).count() == 1
        assert AlertEvent.objects.filter(rule=rule_b).count() == 0

    def test_missing_recipient_marks_event_failed(self):
        asset = make_asset()
        make_score_snapshot(asset, "10x_potential", 90, timezone.now())
        rule = make_rule(asset=asset, metric="score_10x", operator="gt", threshold=80, email="")

        result = process_alerts()

        assert result["fired"] == 0
        assert result["failed"] == 1
        event = AlertEvent.objects.get(rule=rule)
        assert event.status == AlertEvent.Status.FAILED
        assert "no recipient" in event.error_detail.lower()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

class TestNotifications:
    def test_render_alert_message_contains_asset(self):
        asset = make_asset("ETH")
        msg = render_alert_message(asset, "10X Potential score", ">", "80", "90")
        assert "ETH" in msg
        assert "90" in msg

    def test_operator_label(self):
        assert operator_label("gt") == ">"
        assert operator_label("lt") == "<"
        assert operator_label("bogus") == "bogus"

    def test_send_email_via_console_backend(self, settings):
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        from django.core import mail
        from core.notifications import send_email_alert

        send_email_alert("u@example.com", "subject", "body")

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["u@example.com"]

    def test_send_email_requires_recipient(self):
        from core.notifications import send_email_alert
        with pytest.raises(ValueError):
            send_email_alert("", "s", "b")

    def test_send_telegram_requires_token(self, settings):
        settings.TELEGRAM_BOT_TOKEN = ""
        from core.notifications import send_telegram_alert
        with pytest.raises(ValueError):
            send_telegram_alert("12345", "hi")

    def test_send_telegram_requires_chat_id(self, settings):
        settings.TELEGRAM_BOT_TOKEN = "token"
        from core.notifications import send_telegram_alert
        with pytest.raises(ValueError):
            send_telegram_alert("", "hi")


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

def _auth_headers(user_id=USER_ID):
    token = jwt.encode(
        {"user_id": user_id, "email": "user@example.com"},
        settings.JWT_SIGNING_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


class TestAlertRuleAPI:
    def test_requires_subscription(self, client):
        Asset.objects.create(symbol="SOL", name="Solana")
        resp = client.post("/api/v1/alerts/rules/", data={}, content_type="application/json",
                           **_auth_headers("no-subscription-user"))
        assert resp.status_code == 403

    def test_create_rule_scopes_to_authenticated_user(self, client):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=timezone.now() + timedelta(days=30))
        asset = make_asset()
        payload = {
            "name": "SOL 10X",
            "asset": str(asset.id),
            "metric": "score_10x",
            "operator": "gt",
            "threshold": "80",
            "channel": "email",
            "email": "user@example.com",
        }
        resp = client.post("/api/v1/alerts/rules/", data=json.dumps(payload),
                           content_type="application/json", **_auth_headers())

        assert resp.status_code == 201
        rule = AlertRule.objects.get()
        assert rule.user_id == USER_ID
        assert rule.name == "SOL 10X"

    def test_create_rule_requires_recipient_for_channel(self, client):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=timezone.now() + timedelta(days=30))
        asset = make_asset()
        payload = {
            "name": "SOL 10X",
            "asset": str(asset.id),
            "metric": "score_10x",
            "operator": "gt",
            "threshold": "80",
            "channel": "email",
            "email": "",
        }
        resp = client.post("/api/v1/alerts/rules/", data=json.dumps(payload),
                           content_type="application/json", **_auth_headers())
        assert resp.status_code == 400

    def test_list_only_returns_own_rules(self, client):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=timezone.now() + timedelta(days=30))
        make_rule()  # belongs to USER_ID
        make_rule()  # belongs to USER_ID
        alert_other = AlertRule.objects.create(
            user_id="other-user", email="x@x.com", name="other",
            metric="score_10x", operator="gt", threshold=Decimal("80"), channel="email",
        )
        resp = client.get("/api/v1/alerts/rules/", **_auth_headers())

        assert resp.status_code == 200
        data = resp.json()["results"]
        assert len(data) == 2
        assert all(rule["email"] != "x@x.com" for rule in data)

    def test_detail_update_and_delete(self, client):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=timezone.now() + timedelta(days=30))
        asset = make_asset()
        rule = make_rule(asset=asset)
        url = f"/api/v1/alerts/rules/{rule.id}/"

        resp = client.patch(url, data=json.dumps({"threshold": "90"}),
                            content_type="application/json", **_auth_headers())
        assert resp.status_code == 200
        rule.refresh_from_db()
        assert rule.threshold == Decimal("90")

        resp = client.delete(url, **_auth_headers())
        assert resp.status_code == 204
        assert AlertRule.objects.count() == 0

    def test_cannot_modify_another_users_rule(self, client):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=timezone.now() + timedelta(days=30))
        other_rule = AlertRule.objects.create(
            user_id="other-user", email="x@x.com", name="other",
            metric="score_10x", operator="gt", threshold=Decimal("80"), channel="email",
        )
        resp = client.patch(f"/api/v1/alerts/rules/{other_rule.id}/",
                            data=json.dumps({"threshold": "90"}),
                            content_type="application/json", **_auth_headers())
        assert resp.status_code == 404


class TestAlertHistoryAPI:
    def test_history_returns_only_own_events(self, client):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=timezone.now() + timedelta(days=30))
        asset = make_asset()
        rule = make_rule(asset=asset)
        AlertEvent.objects.create(rule=rule, asset=asset, metric="score_10x",
                                  operator="gt", threshold=Decimal("80"),
                                  observed_value=Decimal("90"), status=AlertEvent.Status.SENT)
        other_rule = AlertRule.objects.create(
            user_id="other-user", email="x@x.com", name="other",
            metric="score_10x", operator="gt", threshold=Decimal("80"), channel="email",
        )
        AlertEvent.objects.create(rule=other_rule, asset=asset, metric="score_10x",
                                  operator="gt", threshold=Decimal("80"),
                                  observed_value=Decimal("90"), status=AlertEvent.Status.SENT)

        resp = client.get("/api/v1/alerts/history/", **_auth_headers())

        assert resp.status_code == 200
        data = resp.json()["results"]
        assert len(data) == 1

    def test_history_filters_by_status(self, client):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=timezone.now() + timedelta(days=30))
        asset = make_asset()
        rule = make_rule(asset=asset)
        AlertEvent.objects.create(rule=rule, asset=asset, metric="score_10x",
                                  operator="gt", threshold=Decimal("80"),
                                  observed_value=Decimal("90"), status=AlertEvent.Status.SENT)
        AlertEvent.objects.create(rule=rule, asset=asset, metric="score_10x",
                                  operator="gt", threshold=Decimal("80"),
                                  observed_value=Decimal("90"), status=AlertEvent.Status.FAILED)

        resp = client.get("/api/v1/alerts/history/?status=failed", **_auth_headers())

        assert resp.status_code == 200
        data = resp.json()["results"]
        assert len(data) == 1
        assert data[0]["status"] == "failed"
