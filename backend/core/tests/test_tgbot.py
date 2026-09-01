"""Tests for Feature 8 — the Telegram bot: pure command handlers (/score,
/top, /link, /alerts), the update webhook dispatcher/view, the secure
chat-binding API endpoint, and outbound sendMessage."""

import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import jwt
import pytest
import responses
from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from core.models import (
    AlertRule, Asset, MarketSnapshot, ScoreSnapshot, Subscription, TelegramBinding,
)
from core import tgbot
from core.tgbot import handle_text, handle_update

pytestmark = pytest.mark.django_db

USER_ID = "site-user-42"
CHAT_ID = "123456789"
WEBHOOK_URL = "https://api.telegram.org/botTOKEN/sendMessage"


def make_asset(symbol="SOL"):
    return Asset.objects.create(symbol=symbol, name=symbol.upper(), is_active=True)


def make_market_snapshot(asset, price, market_cap=None, volume=None, observed_at=None):
    return MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal(str(price)),
        market_cap_usd=Decimal(str(market_cap)) if market_cap is not None else None,
        volume_24h_usd=Decimal(str(volume)) if volume is not None else None,
        source="coingecko", observed_at=observed_at or timezone.now(),
    )


def make_score(asset, model_name, score, confidence="1.0"):
    snap = make_market_snapshot(asset, 1.0)
    return ScoreSnapshot.objects.create(
        asset=asset, market_snapshot=snap, model_name=model_name,
        model_version="v1.0", score=Decimal(str(score)),
        data_confidence=Decimal(confidence),
    )


def make_scored_asset(symbol="SOL"):
    asset = make_asset(symbol)
    for m in ["10x_potential", "undervaluation", "momentum", "risk"]:
        make_score(asset, m, 80)
    # A fresh market snapshot (not consumed by a score) for price display.
    make_market_snapshot(asset, 120.0, market_cap=1_200_000_000, volume=50_000_000)
    return asset


def make_verified_binding(chat_id=CHAT_ID, user_id=USER_ID):
    return TelegramBinding.objects.create(
        chat_id=chat_id, user_id=user_id, is_verified=True,
    )


def _auth_headers(user_id=USER_ID):
    token = jwt.encode(
        {"user_id": user_id, "email": "user@example.com"},
        settings.JWT_SIGNING_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def _update(text, chat_id=CHAT_ID, username="bob"):
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": int(chat_id)},
            "from": {"id": 999, "username": username},
            "text": text,
        },
    }


# ---------------------------------------------------------------------------
# Pure handlers
# ---------------------------------------------------------------------------

class TestScoreCommand:
    def test_score_returns_asset_details(self):
        make_scored_asset("SOL")
        replies = handle_text(CHAT_ID, "/score SOL")
        assert len(replies) == 1
        text = replies[0][0]
        assert "SOL" in text
        assert "10X Potential" in text or "3X Growth" in text or "2X Safe" in text
        assert "Risk" in text

    def test_score_case_insensitive(self):
        make_scored_asset("SOL")
        replies = handle_text(CHAT_ID, "/score sol")
        assert "SOL" in replies[0][0]

    def test_score_unknown_symbol(self):
        replies = handle_text(CHAT_ID, "/score NOPE")
        assert "No asset found" in replies[0][0]

    def test_score_without_argument(self):
        replies = handle_text(CHAT_ID, "/score")
        assert "/score <symbol>" in replies[0][0]

    def test_score_unscored_asset_shows_placeholder(self):
        make_asset("NEW")
        replies = handle_text(CHAT_ID, "/score NEW")
        text = replies[0][0]
        assert "NEW" in text
        assert "Unclassified" in text


class TestTopCommand:
    def test_top_ranks_by_10x_score(self):
        low = make_scored_asset("AAA")
        low.score_snapshots.filter(model_name="10x_potential").update(score=Decimal("30"))
        high = make_scored_asset("BBB")
        high.score_snapshots.filter(model_name="10x_potential").update(score=Decimal("95"))
        replies = handle_text(CHAT_ID, "/top")
        text = replies[0][0]
        assert "Top 5" in text
        # Highest 10X first.
        assert text.index("BBB") < text.index("AAA")

    def test_top_n_limits(self):
        make_scored_asset("A")
        make_scored_asset("B")
        make_scored_asset("C")
        replies = handle_text(CHAT_ID, "/top 2")
        text = replies[0][0]
        assert "Top 2" in text
        assert "A — 10X" in text
        assert "B — 10X" in text
        assert "C —" not in text

    def test_top_with_no_scores(self):
        make_asset("SOL")
        replies = handle_text(CHAT_ID, "/top")
        assert "No scored tokens yet" in replies[0][0]


class TestHelpStart:
    def test_start_returns_help_and_keyboard(self):
        replies = handle_text(CHAT_ID, "/start")
        text, markup = replies[0]
        assert "/score" in text
        assert "/top" in text
        assert "/alerts" in text
        assert "/link" in text
        assert "keyboard" in markup

    def test_unknown_command(self):
        replies = handle_text(CHAT_ID, "/frobnicate")
        assert "Try /help" in replies[0][0]

    def test_empty_message(self):
        replies = handle_text(CHAT_ID, "   ")
        assert "Hello" in replies[0][0]


class TestLink:
    def test_link_creates_binding_with_token(self):
        replies = handle_text(CHAT_ID, "/link", {"username": "bob"})
        text = replies[0][0]
        assert "verify_token" in text
        binding = TelegramBinding.objects.get(chat_id=CHAT_ID)
        assert binding.is_verified is False
        assert binding.verify_token
        assert binding.telegram_username == "bob"

    def test_link_rotates_token_each_time(self):
        handle_text(CHAT_ID, "/link")
        first = TelegramBinding.objects.get(chat_id=CHAT_ID).verify_token
        handle_text(CHAT_ID, "/link")
        second = TelegramBinding.objects.get(chat_id=CHAT_ID).verify_token
        assert first != second


class TestAlerts:
    def test_alerts_requires_verified_binding(self):
        replies = handle_text(CHAT_ID, "/alerts")
        assert "isn't linked" in replies[0][0]
        assert "/link" in replies[0][0]

    def test_alerts_empty_list(self):
        make_verified_binding()
        replies = handle_text(CHAT_ID, "/alerts")
        assert "No Telegram alerts" in replies[0][0]

    def test_alerts_add_creates_rule(self):
        make_verified_binding()
        asset = make_asset("SOL")
        replies = handle_text(CHAT_ID, "/alerts add score_10x gt 80 SOL")
        assert "Created alert" in replies[0][0]
        rule = AlertRule.objects.get()
        assert rule.user_id == USER_ID
        assert rule.channel == AlertRule.Channel.TELEGRAM
        assert rule.telegram_chat_id == CHAT_ID
        assert rule.asset_id == asset.id
        assert rule.threshold == Decimal("80")

    def test_alerts_add_all_assets_scope(self):
        make_verified_binding()
        handle_text(CHAT_ID, "/alerts add score_risk lt 20 all")
        rule = AlertRule.objects.get()
        assert rule.asset_id is None
        assert rule.metric == "score_risk"
        assert rule.operator == "lt"

    def test_alerts_add_rejects_bad_metric(self):
        make_verified_binding()
        replies = handle_text(CHAT_ID, "/alerts add nonsense gt 5 SOL")
        assert "Unknown metric" in replies[0][0]
        assert AlertRule.objects.count() == 0

    def test_alerts_add_rejects_bad_operator(self):
        make_verified_binding()
        replies = handle_text(CHAT_ID, "/alerts add score_10x ~ 5 SOL")
        assert "Unknown operator" in replies[0][0]

    def test_alerts_add_rejects_non_numeric_value(self):
        make_verified_binding()
        replies = handle_text(CHAT_ID, "/alerts add score_10x gt abc SOL")
        assert "not a valid number" in replies[0][0]

    def test_alerts_add_rejects_unknown_asset(self):
        make_verified_binding()
        replies = handle_text(CHAT_ID, "/alerts add score_10x gt 80 NOPE")
        assert "No asset found" in replies[0][0]

    def test_alerts_list_shows_rules(self):
        binding = make_verified_binding()
        AlertRule.objects.create(
            user_id=USER_ID, name="SOL rule", asset=make_asset("SOL"),
            metric="score_10x", operator="gt", threshold=Decimal("80"),
            channel=AlertRule.Channel.TELEGRAM, telegram_chat_id=binding.chat_id,
        )
        replies = handle_text(CHAT_ID, "/alerts list")
        assert "Your Telegram Alerts" in replies[0][0]
        assert "80" in replies[0][0]

    def test_alerts_remove_deletes_own_rule(self):
        binding = make_verified_binding()
        rule = AlertRule.objects.create(
            user_id=USER_ID, name="r", metric="score_10x", operator="gt",
            threshold=Decimal("80"), channel=AlertRule.Channel.TELEGRAM,
            telegram_chat_id=binding.chat_id,
        )
        replies = handle_text(CHAT_ID, f"/alerts remove {rule.id}")
        assert "Deleted alert" in replies[0][0]
        assert AlertRule.objects.count() == 0

    def test_alerts_remove_unknown_id(self):
        make_verified_binding()
        replies = handle_text(CHAT_ID, "/alerts remove 00000000-0000-0000-0000-000000000000")
        assert "No Telegram alert" in replies[0][0]

    def test_alerts_remove_only_own_rule(self):
        binding = make_verified_binding()
        other = AlertRule.objects.create(
            user_id="other-user", name="r", metric="score_10x", operator="gt",
            threshold=Decimal("80"), channel=AlertRule.Channel.TELEGRAM,
            telegram_chat_id=binding.chat_id,
        )
        replies = handle_text(CHAT_ID, f"/alerts remove {other.id}")
        assert "No Telegram alert" in replies[0][0]
        assert AlertRule.objects.count() == 1


# ---------------------------------------------------------------------------
# Binding verification API
# ---------------------------------------------------------------------------

class TestBindAPI:
    def _make_pending(self):
        return TelegramBinding.objects.create(chat_id=CHAT_ID, verify_token="tok-123")

    def _subscribe(self):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=timezone.now() + timedelta(days=30))

    def test_verify_binds_chat_to_user(self, client):
        self._subscribe()
        self._make_pending()
        resp = client.post("/api/v1/telegram/verify/",
                           data={"chat_id": CHAT_ID, "verify_token": "tok-123"},
                           content_type="application/json", **_auth_headers())
        assert resp.status_code == 200
        assert resp.json()["user_id"] == USER_ID
        binding = TelegramBinding.objects.get(chat_id=CHAT_ID)
        assert binding.is_verified is True
        assert binding.user_id == USER_ID
        assert binding.verify_token == ""

    def test_verify_rejects_wrong_token(self, client):
        self._subscribe()
        self._make_pending()
        resp = client.post("/api/v1/telegram/verify/",
                           data={"chat_id": CHAT_ID, "verify_token": "wrong"},
                           content_type="application/json", **_auth_headers())
        assert resp.status_code == 400
        binding = TelegramBinding.objects.get(chat_id=CHAT_ID)
        assert binding.is_verified is False

    def test_verify_no_pending_binding(self, client):
        self._subscribe()
        resp = client.post("/api/v1/telegram/verify/",
                           data={"chat_id": "99999", "verify_token": "tok-123"},
                           content_type="application/json", **_auth_headers())
        assert resp.status_code == 404

    def test_verify_requires_subscription(self, client):
        self._make_pending()
        resp = client.post("/api/v1/telegram/verify/",
                           data={"chat_id": CHAT_ID, "verify_token": "tok-123"},
                           content_type="application/json", **_auth_headers("no-sub"))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Update dispatch + webhook
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_handle_update_sends_reply(self):
        make_scored_asset("SOL")
        with patch("core.tgbot.send_telegram_message") as send:
            sent = handle_update(_update("/score SOL"))
        assert len(sent) == 1
        send.assert_called_once()
        args = send.call_args[0]
        assert args[0] == CHAT_ID
        assert "SOL" in args[1]

    def test_handle_update_ignores_non_message_update(self):
        with patch("core.tgbot.send_telegram_message") as send:
            sent = handle_update({"update_id": 5, "callback_query": {"data": "x"}})
        assert sent == []
        send.assert_not_called()

    def test_send_failure_is_best_effort(self):
        make_scored_asset("SOL")
        with patch("core.tgbot.send_telegram_message", side_effect=RuntimeError("boom")):
            sent = handle_update(_update("/score SOL"))
        assert sent == []


class TestWebhookView:
    def test_valid_update_returns_200(self, client):
        make_scored_asset("SOL")
        with patch("core.tgbot.send_telegram_message"):
            resp = client.post("/api/webhooks/telegram/",
                               data=_update("/score SOL"), content_type="application/json")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_invalid_json_returns_400(self, client):
        resp = client.post("/api/webhooks/telegram/", data="not-json",
                           content_type="application/json")
        assert resp.status_code == 400

    def test_secret_token_enforced(self, client):
        with override_settings(TELEGRAM_WEBHOOK_SECRET="hush"):
            with patch("core.tgbot.send_telegram_message") as send:
                resp = client.post("/api/webhooks/telegram/",
                                   data=_update("/help"), content_type="application/json")
            send.assert_not_called()
        assert resp.status_code == 403

    def test_secret_token_allows_correct_value(self, client):
        with override_settings(TELEGRAM_WEBHOOK_SECRET="hush"):
            with patch("core.tgbot.send_telegram_message"):
                resp = client.post(
                    "/api/webhooks/telegram/", data=_update("/help"),
                    content_type="application/json",
                    HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="hush",
                )
        assert resp.status_code == 200


class TestSendMessage:
    @responses.activate
    def test_send_telegram_message_posts_to_bot_api(self):
        with override_settings(TELEGRAM_BOT_TOKEN="TOKEN"):
            responses.add(responses.POST, WEBHOOK_URL, json={"ok": True, "result": {}}, status=200)
            tgbot.send_telegram_message(CHAT_ID, "hi", {"inline_keyboard": []})
        req = responses.calls[0].request
        body = json.loads(req.body)
        assert body["chat_id"] == CHAT_ID
        assert body["text"] == "hi"
        assert body["reply_markup"] == {"inline_keyboard": []}

    @responses.activate
    def test_send_telegram_message_raises_on_api_error(self):
        with override_settings(TELEGRAM_BOT_TOKEN="TOKEN"):
            responses.add(responses.POST, WEBHOOK_URL, json={"ok": False}, status=200)
            with pytest.raises(RuntimeError):
                tgbot.send_telegram_message(CHAT_ID, "hi")

    def test_send_telegram_message_requires_token(self):
        with override_settings(TELEGRAM_BOT_TOKEN=""):
            with pytest.raises(ValueError):
                tgbot.send_telegram_message(CHAT_ID, "hi")
