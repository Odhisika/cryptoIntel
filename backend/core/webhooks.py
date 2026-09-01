"""
Webhook delivery for pushed events (Roadmap Tier 3, Feature 7).

Event types so far:
  - score.changed: an asset's reward tier changed after a scoring run (the
    meaningful signal — a token promoted to / demoted from a tier).

Score changes are GLOBAL events (scores are computed for the whole universe,
not per-user), so dispatch fans out to every matching WebhookSubscription
across all users.

Delivery flow:
  1. A source (e.g. the scoring Celery task) calls dispatch_score_change(...).
  2. We find every active WebhookSubscription that targets that asset (or ALL
     assets) and is opted into the event type.
  3. Each recipient gets a signed POST: header X-Webhook-Signature = HMAC-SHA256
     hex digest of the request body using the subscription's shared secret, so
     the receiving service can verify authenticity.

Delivery is best-effort and never raises out of the caller: failures are
recorded on the subscription (last_status) and logged, so a dead endpoint can't
break a scoring run.
"""

import hashlib
import hmac
import json
import logging

import requests

from core.models import WebhookSubscription

logger = logging.getLogger(__name__)


class Event:
    SCORE_CHANGED = "score.changed"


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _matching_subscriptions(event_type, asset):
    qs = WebhookSubscription.objects.filter(is_active=True)
    return [
        sub for sub in qs
        if sub.asset_id is None or (asset is not None and sub.asset_id == asset.id)
        if sub.accepts_event(event_type)
    ]


def dispatch_score_change(asset, tier_before, tier_after, score_10x, timestamp=None, timeout=10):
    """Deliver a score.changed event to all matching subscriptions.

    No-ops when the tier is unchanged (and no subscriptions match). Returns the
    number of endpoints successfully notified. Never raises.
    """
    if tier_before == tier_after:
        return 0

    payload = {
        "event": Event.SCORE_CHANGED,
        "data": {
            "asset": str(asset.id),
            "symbol": asset.symbol,
            "name": asset.name,
            "tier_before": tier_before,
            "tier_after": tier_after,
            "score_10x": str(score_10x),
            "occurred_at": (timestamp or _now()).isoformat(),
        },
    }
    body_bytes = json.dumps(payload, default=str).encode("utf-8")

    subs = _matching_subscriptions(Event.SCORE_CHANGED, asset)
    delivered = 0
    for sub in subs:
        try:
            _post(sub, body_bytes, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - delivery is best-effort
            logger.exception("Webhook delivery failed sub=%s url=%s", sub.pk, sub.target_url)
            sub.last_status = f"error"
        else:
            delivered += 1
            sub.last_status = "ok"
        _record(sub)

    return delivered


def _post(sub, body_bytes, timeout):
    headers = {"Content-Type": "application/json", "User-Agent": "crypto-intel-webhook/1.0"}
    if sub.secret:
        headers["X-Webhook-Signature"] = _sign(body_bytes, sub.secret)
    resp = requests.post(sub.target_url, data=body_bytes, headers=headers, timeout=timeout)
    resp.raise_for_status()


def _record(sub):
    from django.utils import timezone
    sub.last_delivery_at = timezone.now()
    sub.save(update_fields=["last_delivery_at", "last_status", "updated_at"])


def _now():
    from django.utils import timezone
    return timezone.now()
