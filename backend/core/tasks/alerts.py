"""
Real-Time Alerts Celery task (Roadmap Tier 1, Feature 2).

Runs on a schedule (see CELERY_BEAT_SCHEDULE in config/settings.py) and:

  1. Iterates every active AlertRule (optionally scoped to a set of assets
     recently rescored — see `recent` param).
  2. Computes the rule's metric from the latest snapshots.
  3. If the threshold is crossed AND the rule isn't in cooldown, dispatches
     the alert to the configured channel (email and/or Telegram) and logs an
     AlertEvent.

Delivery is best-effort: a failed send records status=FAILED with error detail
but never aborts the rest of the batch. The task returns counts so the result
is observable from Celery Flower/the logs.
"""

import logging

from celery import shared_task

from core.alerts import (
    get_metric_value,
    metric_label,
    operator_label,
    threshold_crossed,
)
from core.models import AlertEvent, AlertRule, Asset
from core.notifications import (
    render_alert_message,
    send_email_alert,
    send_telegram_alert,
)

logger = logging.getLogger(__name__)


@shared_task
def process_alerts(recent_asset_ids=None, rule_ids=None):
    """Evaluate all active alert rules and fire deliveries for crosses.

    Args:
        recent_asset_ids: optional iterable of asset UUIDs (only newly scored
            assets) to scope evaluation to. When None, all active assets are
            considered, which is the default full-batch behaviour.
        rule_ids: optional iterable of AlertRule UUIDs to evaluate. When None,
            every active rule is evaluated.
    """
    rules = AlertRule.objects.filter(is_active=True)
    if rule_ids is not None:
        rules = rules.filter(id__in=list(rule_ids))

    fired = 0
    failed = 0
    no_data = 0

    for rule in rules:
        assets = _rule_assets(rule, recent_asset_ids)
        for asset in assets:
            value = get_metric_value(asset, rule.metric)
            if value is None:
                no_data += 1
                continue
            if not threshold_crossed(rule.operator, value, rule.threshold):
                continue
            if rule.in_cooldown_for(asset):
                continue

            ok = _dispatch(rule, asset, value)
            if ok:
                fired += 1
            else:
                failed += 1

    return {"fired": fired, "failed": failed, "no_data": no_data}


def _rule_assets(rule, recent_asset_ids):
    """Return the set of assets a rule should be evaluated against."""
    if rule.asset_id is not None:
        return [rule.asset]

    base = Asset.objects.filter(is_active=True)
    if recent_asset_ids is not None:
        base = base.filter(id__in=[str(a) for a in recent_asset_ids])
    return list(base)


def _dispatch(rule, asset, value):
    """Fire a rule to its channels and log an AlertEvent. Returns True on
    success (any channel succeeded), False if all deliveries failed."""
    subject = f"[Crypto Intel Alert] {asset.symbol.upper()} {metric_label(rule.metric)}"
    message = render_alert_message(
        asset=asset,
        metric_label=metric_label(rule.metric),
        operator_label=operator_label(rule.operator),
        threshold=rule.threshold,
        value=value,
    )

    channels = []
    errors = []

    if rule.channel == AlertRule.Channel.EMAIL:
        if rule.email:
            try:
                send_email_alert(rule.email, subject, message)
                channels.append("email")
            except Exception as exc:  # noqa: BLE001 - delivery is best-effort
                errors.append(f"email: {exc}")
                logger.exception("Email alert failed for rule=%s asset=%s", rule.pk, asset.symbol)
        else:
            errors.append("email: no recipient address")

    if rule.channel == AlertRule.Channel.TELEGRAM:
        if rule.telegram_chat_id:
            try:
                send_telegram_alert(rule.telegram_chat_id, message)
                channels.append("telegram")
            except Exception as exc:  # noqa: BLE001 - delivery is best-effort
                errors.append(f"telegram: {exc}")
                logger.exception("Telegram alert failed for rule=%s asset=%s", rule.pk, asset.symbol)
        else:
            errors.append("telegram: no chat id")

    from django.utils import timezone

    rule.last_fired_at = timezone.now()
    rule.save(update_fields=["last_fired_at", "updated_at"])

    status = AlertEvent.Status.FAILED if not channels else AlertEvent.Status.SENT
    AlertEvent.objects.create(
        rule=rule,
        asset=asset,
        metric=rule.metric,
        operator=rule.operator,
        threshold=rule.threshold,
        observed_value=value,
        status=status,
        channels=channels,
        error_detail="; ".join(errors),
    )
    return bool(channels)
