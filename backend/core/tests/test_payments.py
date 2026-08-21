"""Tests for the Paystack webhook handler (core.payments)."""

import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone

from core.models import Subscription

pytestmark = pytest.mark.django_db

WEBHOOK_URL = "/api/webhooks/paystack/"
USER_ID = "site-user-42"


def sign_payload(body: bytes) -> str:
    return hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode("utf-8"), body, hashlib.sha512
    ).hexdigest()


def post_webhook(client, payload: dict, signature=None):
    body = json.dumps(payload).encode("utf-8")
    if signature is None:
        signature = sign_payload(body)
    return client.post(
        WEBHOOK_URL,
        data=body,
        content_type="application/json",
        HTTP_X_PAYSTACK_SIGNATURE=signature,
    )


def charge_success_event(data_overrides=None, **event_overrides):
    event = {
        "event": "charge.success",
        "data": {
            "status": "success",
            "reference": "ps_ref_001",
            "amount": 500000,
            "metadata": {"user_id": USER_ID},
            "customer": {"email": "user@example.com"},
            "plan": {"name": "monthly"},
        },
    }
    if data_overrides:
        event["data"].update(data_overrides)
    event.update(event_overrides)
    return event


class TestSignatureVerification:
    def test_valid_signature_with_charge_success_returns_activated(self, client):
        response = post_webhook(client, charge_success_event())

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "activated"
        assert body["user_id"] == USER_ID
        assert "expires_at" in body

    def test_invalid_signature_returns_400(self, client):
        response = post_webhook(
            client, charge_success_event(), signature="f" * 128
        )

        assert response.status_code == 400
        assert response.json()["error"] == "Invalid signature"
        assert Subscription.objects.count() == 0

    def test_signature_from_wrong_secret_returns_400(self, client):
        body = json.dumps(charge_success_event()).encode("utf-8")
        forged = hmac.new(b"attacker-secret", body, hashlib.sha512).hexdigest()

        response = post_webhook(client, charge_success_event(), signature=forged)

        assert response.status_code == 400
        assert Subscription.objects.count() == 0

    def test_missing_signature_returns_400(self, client):
        body = json.dumps(charge_success_event()).encode("utf-8")

        response = client.post(WEBHOOK_URL, data=body, content_type="application/json")

        assert response.status_code == 400
        assert response.json()["error"] == "Missing signature"
        assert Subscription.objects.count() == 0

    def test_invalid_json_with_valid_signature_returns_400(self, client):
        raw_body = b"{not valid json"
        response = client.post(
            WEBHOOK_URL,
            data=raw_body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=sign_payload(raw_body),
        )

        assert response.status_code == 400
        assert response.json()["error"] == "Invalid JSON"

    def test_get_request_is_not_allowed(self, client):
        response = client.get(WEBHOOK_URL)

        assert response.status_code == 405


class TestEventFiltering:
    def test_ignored_event_type_returns_200_ignored(self, client):
        event = charge_success_event(**{"event": "transfer.success"})

        response = post_webhook(client, event)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ignored"
        assert body["event"] == "transfer.success"
        assert Subscription.objects.count() == 0

    @pytest.mark.parametrize(
        "event_type",
        ["charge.failure", "refund.processed", "subscription.disable", ""],
    )
    def test_various_unhandled_events_are_ignored(self, client, event_type):
        event = charge_success_event(**{"event": event_type})

        response = post_webhook(client, event)

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"
        assert Subscription.objects.count() == 0

    def test_handled_subscription_events_are_accepted(self, client):
        for event_type in ("charge.success", "subscription.create", "invoice.payment_success"):
            response = post_webhook(
                client,
                charge_success_event(
                    data_overrides={"reference": f"ref-{event_type}"},
                    **{"event": event_type},
                ),
            )
            assert response.status_code == 200
            assert response.json()["status"] == "activated"

    def test_failed_payment_status_returns_payment_not_successful(self, client):
        event = charge_success_event(data_overrides={"status": "failed"})

        response = post_webhook(client, event)

        assert response.status_code == 200
        assert response.json()["status"] == "payment_not_successful"
        assert Subscription.objects.count() == 0

    @pytest.mark.parametrize("status", ["abandoned", "pending", "reversed"])
    def test_other_non_success_statuses_are_rejected(self, client, status):
        event = charge_success_event(data_overrides={"status": status})

        response = post_webhook(client, event)

        assert response.status_code == 200
        assert response.json()["status"] == "payment_not_successful"
        assert Subscription.objects.count() == 0


class TestUserIdExtraction:
    def test_missing_user_id_returns_400(self, client):
        event = charge_success_event(data_overrides={"metadata": {}, "customer": {}})

        response = post_webhook(client, event)

        assert response.status_code == 400
        assert response.json()["error"] == "No user_id in metadata"
        assert Subscription.objects.count() == 0

    def test_empty_user_id_falls_back_to_customer_email(self, client):
        event = charge_success_event(
            data_overrides={
                "metadata": {},
                "customer": {"email": "fallback-user@example.com"},
            }
        )

        response = post_webhook(client, event)

        assert response.status_code == 200
        assert response.json()["user_id"] == "fallback-user@example.com"
        subscription = Subscription.objects.get(user_id="fallback-user@example.com")
        assert subscription.email == "fallback-user@example.com"


class TestSubscriptionLifecycle:
    def test_successful_webhook_creates_subscription(self, client):
        before = timezone.now()
        response = post_webhook(client, charge_success_event())

        assert response.status_code == 200
        assert Subscription.objects.count() == 1

        subscription = Subscription.objects.get(user_id=USER_ID)
        assert subscription.status == Subscription.Status.ACTIVE
        assert subscription.email == "user@example.com"
        assert subscription.paystack_reference == "ps_ref_001"
        assert subscription.plan == "monthly"
        expected_expiry = before + timedelta(days=30)
        assert abs((subscription.expires_at - expected_expiry).total_seconds()) < 5
        assert abs((subscription.starts_at - before).total_seconds()) < 5
        assert subscription.is_valid is True

    def test_repeat_payment_updates_existing_subscription(self, client):
        post_webhook(client, charge_success_event())
        first = Subscription.objects.get(user_id=USER_ID)
        first_expiry = first.expires_at
        first_reference = first.paystack_reference

        second_event = charge_success_event(
            data_overrides={
                "reference": "ps_ref_002",
                "plan": {"name": "yearly"},
                "customer": {"email": "new-email@example.com"},
            }
        )
        response = post_webhook(client, second_event)

        assert response.status_code == 200
        assert Subscription.objects.filter(user_id=USER_ID).count() == 1

        updated = Subscription.objects.get(user_id=USER_ID)
        assert updated.pk == first.pk
        assert updated.paystack_reference == "ps_ref_002"
        assert updated.paystack_reference != first_reference
        assert updated.plan == "yearly"
        assert updated.email == "new-email@example.com"
        assert updated.expires_at > first_expiry
        assert updated.is_valid is True

    def test_repeat_payment_reactivates_expired_subscription(self, client):
        post_webhook(client, charge_success_event())
        subscription = Subscription.objects.get(user_id=USER_ID)
        subscription.status = Subscription.Status.EXPIRED
        subscription.save()

        response = post_webhook(client, charge_success_event())

        assert response.status_code == 200
        subscription.refresh_from_db()
        assert subscription.status == Subscription.Status.ACTIVE
        assert subscription.is_valid is True
