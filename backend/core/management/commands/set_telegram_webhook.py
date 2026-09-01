"""Register (or unregister) the Telegram bot webhook.

Usage:
  python manage.py set_telegram_webhook [--url URL] [--unset]

  --url    Public webhook URL; defaults to settings.TELEGRAM_PUBLIC_BASE_URL
           + /api/webhooks/telegram/. If a TELEGRAM_WEBHOOK_SECRET is set it
           is passed to Telegram so that only the bot can call us.
  --unset  Remove the currently configured webhook.
"""

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Register or unregister the Telegram bot webhook with the Bot API."

    def add_arguments(self, parser):
        parser.add_argument("--url", default="", help="Public webhook URL (defaults to TELEGRAM_PUBLIC_BASE_URL + path).")
        parser.add_argument("--unset", action="store_true", help="Remove the webhook instead of setting it.")

    def handle(self, *args, **options):
        token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise CommandError("TELEGRAM_BOT_TOKEN is not configured.")

        api = f"https://api.telegram.org/bot{token}/{{method}}"

        if options["unset"]:
            resp = requests.post(api.format(method="deleteWebhook") + "?drop_pending_updates=true", timeout=10)
            self._check(resp)
            self.stdout.write(self.style.SUCCESS("Webhook removed."))
            return

        base = (options["url"] or "").strip() or settings.TELEGRAM_PUBLIC_BASE_URL
        url = base.rstrip("/") + "/api/webhooks/telegram/"

        payload = {"url": url}
        secret = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
        if secret:
            payload["secret_token"] = secret

        resp = requests.post(api.format(method="setWebhook"), json=payload, timeout=10)
        self._check(resp)
        self.stdout.write(self.style.SUCCESS(f"Webhook set -> {url}"))

    @staticmethod
    def _check(resp):
        try:
            data = resp.json()
        except ValueError:
            raise CommandError(f"Telegram returned invalid response: {resp.status_code}")
        if not data.get("ok"):
            raise CommandError(f"Telegram error: {data}")
