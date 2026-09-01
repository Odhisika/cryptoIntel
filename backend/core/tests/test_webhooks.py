"""Tests for Feature 7's webhook delivery + B2B usage tracking:
score-change webhook model/dispatch, the webhook CRUD API, and per-1,000-call
usage accounting."""

import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal

import jwt
import pytest
import responses
from django.conf import settings
from django.utils import timezone

from core.models import (
    ApiUsage,
    Asset,
    Subscription,
    WebhookSubscription,
)
from core.webhooks import Event, dispatch_score_change

pytestmark = pytest.mark.django_db

USER_ID = "site-user-42"
WEBHOOK_URL = "https://analytics.example.com/hooks/score-changed"


def make_asset(symbol="SOL"):
    return Asset.objects.create(symbol=symbol, name=symbol.upper(), is_active=True)


def make_subscription(asset=None, user_id=USER_ID, secret="s3cret",
                      event_types=None, is_active=True, target_url=WEBHOOK_URL):
    if event_types is None:
        event_types = ["score.changed"]
    return WebhookSubscription.objects.create(
        user_id=user_id, name="Analytics", target_url=target_url,
        secret=secret, asset=asset, event_types=event_types,
        is_active=is_active,
    )


def _auth_headers(user_id=USER_ID):
    token = jwt.encode(
        {"user_id": user_id, "email": "user@example.com"},
        settings.JWT_SIGNING_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class TestWebhookSubscriptionModel:
    def test_empty_event_types_accepts_everything(self):
        sub = make_subscription(event_types=[])
        assert sub.accepts_event("score.changed")
        assert sub.accepts_event("anything.else")

    def test_specific_event_types_filter(self):
        sub = make_subscription(event_types=["score.changed"])
        assert sub.accepts_event("score.changed")
        assert not sub.accepts_event("price.spike")


class TestDispatchScoreChange:
    def test_noop_when_tier_unchanged(self):
        asset = make_asset()
        make_subscription(asset=asset)
        # No responses registered — if a request were attempted this would fail.
        assert dispatch_score_change(asset, "safe_2x", "safe_2x", Decimal("70")) == 0

    @responses.activate
    def test_delivers_signed_post_to_matching_subscription(self):
        asset = make_asset()
        sub = make_subscription(asset=asset, secret="s3cret")
        responses.add(responses.POST, sub.target_url, status=200)

        delivered = dispatch_score_change(asset, "safe_2x", "growth_3x", Decimal("70"))

        assert delivered == 1
        assert len(responses.calls) == 1
        req = responses.calls[0].request
        assert req.headers["X-Webhook-Signature"] == hmac.new(
            b"s3cret", req.body, hashlib.sha256
        ).hexdigest()

        body = json.loads(req.body)
        assert body["event"] == "score.changed"
        assert body["data"]["symbol"] == "SOL"
        assert body["data"]["tier_before"] == "safe_2x"
        assert body["data"]["tier_after"] == "growth_3x"

        sub.refresh_from_db()
        assert sub.last_status == "ok"
        assert sub.last_delivery_at is not None

    @responses.activate
    def test_all_asset_subscription_receives_any_score_change(self):
        asset = make_asset()
        make_subscription(asset=None)  # ALL assets
        responses.add(responses.POST, WEBHOOK_URL, status=200)

        delivered = dispatch_score_change(asset, "safe_2x", "growth_3x", Decimal("70"))

        assert delivered == 1

    @responses.activate
    def test_specific_asset_subscription_ignores_other_asset(self):
        asset_a = make_asset("AAA")
        make_subscription(asset=make_asset("BBB"), secret="other")
        responses.add(responses.POST, WEBHOOK_URL, status=200)

        delivered = dispatch_score_change(asset_a, "safe_2x", "growth_3x", Decimal("70"))

        assert delivered == 0
        assert len(responses.calls) == 0

    @responses.activate
    def test_inactive_subscriptions_are_skipped(self):
        asset = make_asset()
        make_subscription(asset=asset, is_active=False)
        responses.add(responses.POST, WEBHOOK_URL, status=200)

        delivered = dispatch_score_change(asset, "safe_2x", "growth_3x", Decimal("70"))

        assert delivered == 0
        assert len(responses.calls) == 0

    @responses.activate
    def test_subscriptions_opted_out_of_event_are_skipped(self):
        asset = make_asset()
        make_subscription(asset=asset, event_types=["price.spike"])
        responses.add(responses.POST, WEBHOOK_URL, status=200)

        delivered = dispatch_score_change(asset, "safe_2x", "growth_3x", Decimal("70"))

        assert delivered == 0

    @responses.activate
    def test_transport_failure_does_not_raise_and_marks_error(self):
        asset = make_asset()
        sub = make_subscription(asset=asset)
        responses.add(responses.POST, sub.target_url, status=500)

        # Must NOT raise despite the 5xx — delivery is best-effort.
        delivered = dispatch_score_change(asset, "safe_2x", "growth_3x", Decimal("70"))

        assert delivered == 0
        sub.refresh_from_db()
        assert sub.last_status == "error"
        assert sub.last_delivery_at is not None


# ---------------------------------------------------------------------------
# Webhook CRUD API
# ---------------------------------------------------------------------------

class TestWebhookAPI:
    def test_requires_subscription(self, client):
        resp = client.post("/api/v1/webhooks/", data={}, content_type="application/json",
                           **_auth_headers("no-subscription-user"))
        assert resp.status_code == 403

    def test_create_webhook_scoped_to_authenticated_user(self, client):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=timezone.now() + timedelta(days=30))
        _auth_headers()
        payload = {"name": "Prod analytics", "target_url": WEBHOOK_URL, "secret": "abc"}
        resp = client.post("/api/v1/webhooks/", data=json.dumps(payload),
                           content_type="application/json", **_auth_headers())
        assert resp.status_code == 201
        sub = WebhookSubscription.objects.get()
        assert sub.user_id == USER_ID
        assert sub.asset_id is None  # ALL assets

    def test_create_rejects_unknown_event_type(self, client):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=timezone.now() + timedelta(days=30))
        payload = {"name": "x", "target_url": WEBHOOK_URL,
                   "event_types": ["score.changed", "not.a.real.event"]}
        resp = client.post("/api/v1/webhooks/", data=json.dumps(payload),
                           content_type="application/json", **_auth_headers())
        assert resp.status_code == 400

    def test_list_only_returns_own_webhooks(self, client):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=timezone.now() + timedelta(days=30))
        make_subscription()
        WebhookSubscription.objects.create(user_id="other-user", name="Other",
                                           target_url=WEBHOOK_URL, event_types=["score.changed"])
        resp = client.get("/api/v1/webhooks/", **_auth_headers())
        assert resp.status_code == 200
        data = resp.json()["results"]
        assert len(data) == 1
        assert data[0]["name"] == "Analytics"

    def test_detail_update_and_delete(self, client):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=timezone.now() + timedelta(days=30))
        sub = make_subscription()
        url = f"/api/v1/webhooks/{sub.id}/"
        resp = client.patch(url, data=json.dumps({"name": "renamed"}),
                            content_type="application/json", **_auth_headers())
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.name == "renamed"
        resp = client.delete(url, **_auth_headers())
        assert resp.status_code == 204
        assert WebhookSubscription.objects.count() == 0

    def test_cannot_modify_another_users_webhook(self, client):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=timezone.now() + timedelta(days=30))
        other = WebhookSubscription.objects.create(
            user_id="other-user", name="Other", target_url=WEBHOOK_URL,
            event_types=["score.changed"])
        resp = client.patch(f"/api/v1/webhooks/{other.id}/",
                            data=json.dumps({"name": "hijack"}),
                            content_type="application/json", **_auth_headers())
        assert resp.status_code == 404

    def test_secret_is_write_only(self, client):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=timezone.now() + timedelta(days=30))
        make_subscription(secret="super-secret")
        resp = client.get("/api/v1/webhooks/", **_auth_headers())
        assert resp.status_code == 200
        data = resp.json()["results"][0]
        assert "super-secret" not in json.dumps(data)


# ---------------------------------------------------------------------------
# B2B usage tracking
# ---------------------------------------------------------------------------

class TestApiUsage:
    def test_authenticated_api_request_increments_usage(self, client):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=timezone.now() + timedelta(days=30))
        Asset.objects.create(symbol="SOL", name="Solana", is_active=True)
        resp = client.get("/api/v1/assets/", **_auth_headers())
        assert resp.status_code == 200
        usage = ApiUsage.objects.get(user_id=USER_ID)
        assert usage.call_count == 1

    def test_multiple_requests_accumulate(self, client):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=timezone.now() + timedelta(days=30))
        Asset.objects.create(symbol="SOL", name="Solana", is_active=True)
        client.get("/api/v1/assets/", **_auth_headers())
        client.get("/api/v1/assets/", **_auth_headers())
        usage = ApiUsage.objects.get(user_id=USER_ID)
        assert usage.call_count == 2

    def test_usage_api_returns_own_counts(self, client):
        Subscription.objects.create(user_id=USER_ID, status=Subscription.Status.ACTIVE,
                                    expires_at=timezone.now() + timedelta(days=30))
        ApiUsage.objects.create(user_id=USER_ID, date=timezone.localdate(), call_count=7)
        ApiUsage.objects.create(user_id="other-user", date=timezone.localdate(), call_count=999)
        resp = client.get("/api/v1/usage/", **_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        # 7 seeded + the usage request itself counts as 1 authenticated call.
        assert data[0]["call_count"] == 8

    def test_usage_api_requires_subscription(self, client):
        resp = client.get("/api/v1/usage/", **_auth_headers("no-subscription-user"))
        assert resp.status_code == 403

    def test_usage_does_not_count_unauthenticated_requests(self, client):
        # A non-API / unauthenticated hit should not create usage rows.
        resp = client.get("/api/v1/assets/")
        assert resp.status_code in (401, 403)
        assert ApiUsage.objects.count() == 0
