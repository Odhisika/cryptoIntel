"""
Telegram bot (Roadmap Tier 3, Feature 8).

The bot is an INBOUND channel: Telegram POSTs updates (user messages) to our
/webhooks/telegram/ endpoint, and the bot replies by calling the Bot API's
sendMessage. This is the opposite direction from the alert channel in
core/notifications.py (which pushes outbound messages).

Identity/binding (see TelegramBinding):
  - /score and /top are public and never require identity.
  - /alerts requires the chat to be bound to a user account. The user runs
    /link, which generates a one-time verify_token; they then POST it to the
    JWT-authenticated /api/v1/telegram/verify/ endpoint, which flips the
    binding to verified. Because that endpoint authenticates the caller, only
    the account owner can attach a chat to their user_id.

Structure:
  - handle_text(...) is PURE: given a chat and message text it returns the
    (message, reply_markup) pairs to send. It does no network I/O, so it is
    trivially unit-testable.
  - handle_update(...) / dispatch_text(...) feed Telegram update payloads into
    handle_text and perform the outbound sendMessage. Network failures never
    raise to the caller — delivery is best-effort.
"""

import logging
import secrets
from decimal import Decimal

from django.conf import settings

from core.alerts import METRIC_LABELS, OPERATOR_LABELS
from core.models import (
    AlertRule, Asset, ScoreSnapshot, TelegramBinding,
)
from core.scoring.tiers import classify_tier

logger = logging.getLogger(__name__)


TELEGRAM_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"
BOT = "Crypto Intel Bot"

COMMANDS_HELP = (
    "🤖 *Crypto Intel Bot*\n\n"
    "/score <symbol> — instant score for a token\n"
    "/top [n] — top 10X candidates (default 5)\n"
    "/alerts — manage your Telegram alerts\n"
    "/link — connect this chat to your account\n"
    "/help — show this message"
)


def send_telegram_message(chat_id: str, text: str, reply_markup=None) -> None:
    """Send a message to a Telegram chat via the Bot API. Raises on failure."""
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not configured")
    if not chat_id:
        raise ValueError("No Telegram chat id")

    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup

    import requests
    resp = requests.post(TELEGRAM_SEND_URL.format(token=token), json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")


# ---------------------------------------------------------------------------
# Pure command handlers — return [(text, reply_markup_or_None), ...]
# ---------------------------------------------------------------------------

def handle_text(chat_id: str, text: str, from_user=None) -> list:
    """Parse a user message and produce reply pairs. Pure: no network I/O."""
    clean = (text or "").strip()
    if not clean:
        return [("Hello! Send /help to see what I can do.", _main_keyboard())]

    parts = clean.split()
    cmd = parts[0].lower()

    if cmd in ("/start", "/help", "/help@"):
        return [(COMMANDS_HELP, _main_keyboard())]

    if cmd == "/score":
        return [_reply_score(parts[1] if len(parts) > 1 else "")]

    if cmd == "/top":
        return [_reply_top(parts[1] if len(parts) > 1 else "")]

    if cmd == "/link":
        return [_reply_link(chat_id, from_user)]

    if cmd == "/alerts":
        return _reply_alerts(chat_id, parts)

    return [(f"Sorry, I don't understand `{clean}`. Try /help.", _main_keyboard())]


def _main_keyboard():
    return {
        "keyboard": [["/top", "/score BTC"], ["/alerts", "/link"]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def _reply_score(symbol: str):
    if not symbol:
        return ("Send /score <symbol> — e.g. `/score SOL`.", None)

    asset = _find_asset(symbol)
    if asset is None:
        return (f"No asset found for `{symbol.upper()}`.", None)

    return (_format_asset(asset), None)


def _reply_top(arg: str):
    try:
        n = int(arg) if arg.strip().lstrip("-").isdigit() else 5
    except ValueError:
        n = 5
    n = max(1, min(n, 20))

    rows = _top_candidates(n)
    if not rows:
        return ("No scored tokens yet. Run a scoring pass first.", None)

    lines = [f"🏆 *Top {n} 10X Candidates*"]
    for asset, score, tier in rows:
        lines.append(f"{asset.symbol.upper()} — 10X {_num(score)} · {tier}\n  {asset.name}")
    return ("\n\n".join(lines), None)


def _reply_link(chat_id: str, from_user):
    binding, created = TelegramBinding.objects.get_or_create(
        chat_id=chat_id,
        defaults={
            "user_id": "",
            "telegram_username": (from_user or {}).get("username", "") or "",
            "verify_token": _new_token(),
        },
    )
    # Always rotate the token so an old, possibly leaked token is invalidated.
    binding.verify_token = _new_token()
    if (from_user or {}).get("username"):
        binding.telegram_username = from_user["username"]
    binding.save(update_fields=["verify_token", "telegram_username", "updated_at"])

    return (
        f"🔗 *Link this chat to your account*\n\n"
        f"1. Grab your verification token:\n`{binding.verify_token}`\n\n"
        f"2. POST it to the API as the authenticated user:\n"
        f"`POST /api/v1/telegram/verify/`\n"
        f"`{{\"chat_id\": {chat_id!r}, \"verify_token\": \"{binding.verify_token}\"}}`\n\n"
        f"Once verified, `/alerts` becomes available.",
        None,
    )


def _reply_alerts(chat_id: str, parts: list):
    user_id = _verified_user(chat_id)
    if user_id is None:
        return [(
            "Your Telegram chat isn't linked to an account yet. Run /link to "
            "connect it before managing alerts.",
            None,
        )]

    sub = parts[1].lower() if len(parts) > 1 else "list"

    if sub in ("list", ""):
        return [_alerts_list(user_id)]

    if sub == "add":
        return [_alerts_add(user_id, chat_id, parts[2:])]

    if sub == "remove":
        return [_alerts_remove(user_id, parts[2:])]

    return [(  # heading typo correction omitted; help text instead
        "Usage:\n"
        "`/alerts` — list your alerts\n"
        "`/alerts add <metric> <operator> <value> [symbol|all]`\n"
        "`/alerts remove <id>`\n\n"
        "Metrics: score_10x, score_undervaluation, score_momentum, score_risk, "
        "market_cap_pct_change_24h, volume_pct_change_24h\n"
        "Operators: gt, gte, lt, lte",
        None,
    )]


def _alerts_list(user_id: str):
    rules = (
        AlertRule.objects
        .filter(user_id=user_id, channel=AlertRule.Channel.TELEGRAM)
        .select_related("asset")
        .order_by("-updated_at")
    )
    if not rules:
        return ("No Telegram alerts yet. Add one with\n`/alerts add score_10x gt 80 SOL`", None)

    lines = ["📋 *Your Telegram Alerts*"]
    for r in rules:
        scope = r.asset.symbol.upper() if r.asset else "ALL"
        lines.append(
            f"`{r.id}` {scope} — {METRIC_LABELS.get(r.metric, r.metric)} "
            f"{OPERATOR_LABELS.get(r.operator, r.operator)} {r.threshold}"
            f"{' · off' if not r.is_active else ''}"
        )
    return ("\n".join(lines), None)


def _alerts_add(user_id: str, chat_id: str, args: list):
    if len(args) not in (3, 4):
        return (("Use `/alerts add <metric> <operator> <value> [symbol|all]`."), None)

    metric, operator, value = args[0].lower(), args[1].lower(), args[2]
    scope = args[3].lower() if len(args) == 4 else "all"

    if metric not in AlertRule.Metric.values:
        return (f"Unknown metric `{metric}`. See /help.", None)
    if operator not in AlertRule.Operator.values:
        return (f"Unknown operator `{operator}`. Use gt, gte, lt, lte.", None)

    try:
        threshold = float(value)
    except ValueError:
        return (f"`{value}` is not a valid number.", None)

    asset = None
    if scope != "all":
        asset = _find_asset(scope)
        if asset is None:
            return (f"No asset found for `{scope}`. Use `all` for every token.", None)

    rule = AlertRule.objects.create(
        user_id=user_id,
        name=f"{scope.upper()} {metric} {operator} {threshold}",
        asset=asset,
        metric=metric,
        operator=operator,
        threshold=threshold,
        channel=AlertRule.Channel.TELEGRAM,
        telegram_chat_id=chat_id,
        is_active=True,
    )
    return (f"✅ Created alert `{rule.id}`\n`/alerts` to see it.", None)


def _alerts_remove(user_id: str, args: list):
    if not args:
        return ("Use `/alerts remove <id>`.", None)
    rid = args[0]
    count, _ = (
        AlertRule.objects
        .filter(id=rid, user_id=user_id, channel=AlertRule.Channel.TELEGRAM)
        .delete()
    )
    if count:
        return (f"Deleted alert `{rid}`.", None)
    return (f"No Telegram alert with id `{rid}` found.", None)


# ---------------------------------------------------------------------------
# Network dispatch
# ---------------------------------------------------------------------------

def handle_update(update: dict) -> list:
    """Process one Telegram Update payload. Returns the list of reply texts
    that were sent. Delivery is best-effort — a failed send is logged, never
    raised back to the webhook."""
    message = (update or {}).get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text")
    from_user = message.get("from") or {}

    if chat_id is None or text is None:
        return []

    sent = []
    for reply, markup in handle_text(str(chat_id), text, from_user):
        try:
            send_telegram_message(str(chat_id), reply, markup)
            sent.append(reply)
        except Exception:
            logger.exception("Telegram bot reply failed chat=%s", chat_id)
    return sent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_token() -> str:
    return secrets.token_urlsafe(24)


def _verified_user(chat_id: str):
    try:
        binding = TelegramBinding.objects.get(chat_id=str(chat_id))
    except TelegramBinding.DoesNotExist:
        return None
    return binding.user_id if binding.is_verified and binding.user_id else None


def _find_asset(query: str) -> Asset | None:
    q = query.strip().upper()
    asset = Asset.objects.filter(symbol__iexact=q).first()
    if asset is None:
        asset = Asset.objects.filter(name__iexact=q).first()
    return asset


def _latest_score(asset, model_name) -> ScoreSnapshot | None:
    return (
        ScoreSnapshot.objects
        .filter(asset=asset, model_name=model_name)
        .order_by("-computed_at")
        .first()
    )


def _format_asset(asset) -> str:
    scores = {}
    for m in ["10x_potential", "undervaluation", "momentum", "risk"]:
        snap = _latest_score(asset, m)
        if snap:
            scores[m] = snap

    lines = [f"*{asset.name}* ({asset.symbol.upper()})"]
    lines.append(f"Sector: {asset.get_sector_display() if asset.sector else '-'}")

    snap = asset.market_snapshots.order_by("-observed_at").first()
    if snap:
        lines.append(
            f"Price: *{_fmt(snap.price_usd, '')}*  Cap: {_fmt(snap.market_cap_usd)}  "
            f"Vol(24h): {_fmt(snap.volume_24h_usd)}"
        )

    for label, key in [
        ("10X Potential", "10x_potential"),
        ("Undervaluation", "undervaluation"),
        ("Momentum", "momentum"),
        ("Risk", "risk"),
    ]:
        if key in scores:
            lines.append(f"• {label}: *{_num(scores[key].score)}*")
        else:
            lines.append(f"• {label}: -")

    tier = _classify(scores)
    if tier:
        lines.append(f"\n🏷 Tier: *{tier['label']}* ({tier['confidence']} confidence)")
        if tier["reasoning"]:
            lines.append("· " + "; ".join(tier["reasoning"][:3]))
    else:
        lines.append("\n🏷 Tier: *Unclassified* — not enough data yet.")

    return "\n".join(lines)


def _classify(scores):
    if not scores:
        return None
    result = classify_tier(
        score_10x=scores.get("10x_potential").score if "10x_potential" in scores else None,
        score_risk=scores.get("risk").score if "risk" in scores else None,
        score_momentum=scores.get("momentum").score if "momentum" in scores else None,
        score_undervaluation=scores.get("undervaluation").score if "undervaluation" in scores else None,
        data_confidence_10x=scores.get("10x_potential").data_confidence if "10x_potential" in scores else 0,
        data_confidence_risk=scores.get("risk").data_confidence if "risk" in scores else 0,
        data_confidence_momentum=scores.get("momentum").data_confidence if "momentum" in scores else 0,
        data_confidence_undervaluation=scores.get("undervaluation").data_confidence if "undervaluation" in scores else 0,
    )
    return {
        "label": result.label,
        "confidence": _num(result.confidence),
        "reasoning": result.reasoning,
    }


def _top_candidates(n: int):
    rows = []
    for asset in Asset.objects.filter(is_active=True):
        s10x = _latest_score(asset, "10x_potential")
        if s10x is None:
            continue
        scores = {"10x_potential": s10x}
        for m in ["undervaluation", "momentum", "risk"]:
            snap = _latest_score(asset, m)
            if snap:
                scores[m] = snap
        tier = "Unclassified"
        cls = _classify(scores)
        if cls:
            tier = cls["label"]
        rows.append((asset, s10x.score, tier))
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows[:n]


def _num(val, decimals=1):
    if val is None:
        return "-"
    return f"{float(val):.{decimals}f}"


def _fmt(val, prefix="$", decimals=2):
    if val is None:
        return "-"
    d = Decimal(str(val))
    if d >= 1_000_000_000:
        return f"{prefix}{d / 1_000_000_000:,.{decimals}f}B"
    if d >= 1_000_000:
        return f"{prefix}{d / 1_000_000:,.{decimals}f}M"
    if d >= 1_000:
        return f"{prefix}{d:,.{decimals}f}"
    return f"{prefix}{d:.{max(decimals, 4)}f}"


import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


@csrf_exempt
@require_POST
def telegram_webhook(request):
    """Receive a Telegram Update (user message) and have the bot respond.

    Optional hardening: if settings.TELEGRAM_WEBHOOK_SECRET is set, Telegram
    includes it as the X-Telegram-Bot-Api-Secret-Token header on every poll /
    webhook call; we reject any request without the matching value so random
    callers can't drive the bot.
    """
    secret = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
    if secret and request.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != secret:
        return JsonResponse({"error": "Invalid secret token"}, status=403)

    try:
        update = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not isinstance(update, dict):
        return JsonResponse({"error": "Invalid update"}, status=400)

    reply_count = len(handle_update(update))
    return JsonResponse({"ok": True, "replies_sent": reply_count})
