"""
Notification delivery for Real-Time Alerts (Roadmap Tier 1, Feature 2).

Two channels, both best-effort:
  - Email: sent through Django's email backend. Wire it to SendGrid/SES in
    production via EMAIL_BACKEND/EMAIL_HOST etc.; the console backend is
    the dev default (prints to the log, no external send).
  - Telegram: an outbound webhook POST to the Bot API's sendMessage. This
    expects a Telegram bot whose token is configured (settings.TELEGRAM_BOT_TOKEN).
    The bot itself is a separate process; the platform only needs to be able to
    reach api.telegram.org to deliver a chat message.

Every function returns the drafted message body so callers can persist it to
an AlertEvent. Failures raise so the caller can mark the event as failed —
they should never unwind the whole alert evaluation loop.
"""

import logging

import requests
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)

TELEGRAM_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"


def render_alert_message(asset, metric_label, operator_label, threshold, value) -> str:
    """Build the human-readable alert text shared by email + Telegram."""
    return (
        f"🚨 Crypto Intel Alert: {asset.name} ({asset.symbol.upper()})\n"
        f"{metric_label} {operator_label} {threshold}\n"
        f"Current value: {value}"
    )


def send_email_alert(to_email: str, subject: str, message: str) -> None:
    """Send an alert email. Raises on failure so the caller can record it."""
    if not to_email:
        raise ValueError("No recipient email address")
    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[to_email],
        fail_silently=False,
    )


def send_telegram_alert(chat_id: str, message: str) -> None:
    """Send a message to a Telegram chat via the Bot API webhook. Raises on
    failure (non-2xx / transport error) so the caller can record it."""
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not configured")
    if not chat_id:
        raise ValueError("No Telegram chat id")

    url = TELEGRAM_SEND_URL.format(token=token)
    resp = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API error: {payload}")
