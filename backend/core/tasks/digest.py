"""Weekly Email Digest task (Roadmap Tier 3, Feature 9).

Runs on a weekly Celery Beat schedule and emails the digest to every active
subscriber. A single recipient's delivery failure is isolated (logged, not
raised), so one bad mailbox never cancels the rest of the send.
"""

import logging

from celery import shared_task

from core.digest import send_weekly_digest

logger = logging.getLogger(__name__)


@shared_task
def send_weekly_email_digest():
    return send_weekly_digest()