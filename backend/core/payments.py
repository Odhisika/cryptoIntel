"""
Paystack webhook handler.

Your main site calls this endpoint after Paystack confirms payment.
The webhook verifies the signature, then activates/extends the subscription.
"""

import hashlib
import hmac
import json

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from datetime import timedelta

from core.models import Subscription


SUBSCRIPTION_DURATION = timedelta(days=30)


def verify_paystack_signature(payload_bytes: bytes, signature: str) -> bool:
    """Verify Paystack webhook signature using HMAC-SHA512."""
    secret = settings.PAYSTACK_SECRET_KEY.encode("utf-8")
    computed = hmac.new(secret, payload_bytes, hashlib.sha512).hexdigest()
    return hmac.compare_digest(computed, signature)


def extract_user_id(event_data: dict) -> str | None:
    """Extract user_id from Paystack metadata.

    Expects the user_id to be passed as metadata.user_id when
    initializing the Paystack transaction from the main site.
    """
    metadata = event_data.get("metadata", {})
    user_id = metadata.get("user_id", "")
    if not user_id:
        # Fallback: try customer email
        customer = event_data.get("customer", {})
        user_id = customer.get("email", "")
    return user_id or None


@csrf_exempt
@require_POST
def paystack_webhook(request):
    """Handle Paystack webhook events.

    Expected flow:
    1. User pays on main site via Paystack
    2. Paystack calls this webhook
    3. We verify signature + activate subscription
    """
    signature = request.headers.get("X-Paystack-Signature", "")
    if not signature:
        return JsonResponse({"error": "Missing signature"}, status=400)

    if not verify_paystack_signature(request.body, signature):
        return JsonResponse({"error": "Invalid signature"}, status=400)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    event_type = payload.get("event", "")

    # We only care about successful charges
    if event_type not in ("charge.success", "subscription.create", "invoice.payment_success"):
        return JsonResponse({"status": "ignored", "event": event_type}, status=200)

    event_data = payload.get("data", {})

    # Check payment status
    status = event_data.get("status", "")
    if status != "success":
        return JsonResponse({"status": "payment_not_successful"}, status=200)

    user_id = extract_user_id(event_data)
    if not user_id:
        return JsonResponse({"error": "No user_id in metadata"}, status=400)

    reference = event_data.get("reference", "")
    plan = event_data.get("plan", {})
    plan_name = plan.get("name", "monthly") if isinstance(plan, dict) else "monthly"
    email = event_data.get("customer", {}).get("email", "")

    now = timezone.now()

    subscription, created = Subscription.objects.update_or_create(
        user_id=user_id,
        defaults={
            "email": email,
            "status": Subscription.Status.ACTIVE,
            "paystack_reference": reference,
            "plan": plan_name,
            "starts_at": now,
            "expires_at": now + SUBSCRIPTION_DURATION,
        },
    )

    return JsonResponse({
        "status": "activated",
        "user_id": user_id,
        "expires_at": subscription.expires_at.isoformat(),
    })
