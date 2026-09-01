"""Send the weekly email digest once, on demand.

Usage:
  python manage.py send_weekly_digest
  python manage.py send_weekly_digest --email you@example.com   # single recipient
  python manage.py send_weekly_digest --dry-run                 # print, don't email
"""

from django.core.management.base import BaseCommand

from core.digest import build_digest_text, send_weekly_digest


class Command(BaseCommand):
    help = "Compose and email the weekly Crypto Intel digest."

    def add_arguments(self, parser):
        parser.add_argument("--email", default="", help="Send only to this address (instead of all subscribers).")
        parser.add_argument("--dry-run", action="store_true", help="Print the digest instead of emailing it.")

    def handle(self, *args, **options):
        if options["dry_run"]:
            self.stdout.write(build_digest_text())
            return

        if options["email"]:
            from core.digest import subscriber_emails
            from django.core.mail import send_mail
            text = build_digest_text(as_html=False)
            html = build_digest_text(as_html=True)
            send_mail(
                subject="Crypto Intel — Weekly Digest",
                message=text,
                from_email="Crypto Intel <digest@example.com>",
                recipient_list=[options["email"]],
                html_message=html,
            )
            self.stdout.write(self.style.SUCCESS(f"Digest sent to {options['email']}"))
            return

        result = send_weekly_digest()
        self.stdout.write(
            self.style.SUCCESS(
                f"Digest sent to {result['sent']}/{result['recipients']} "
                f"subscribers (top={result['top_count']}, regime={result['regime']}, "
                f"up={result['movers_up']}, down={result['movers_down']})."
            )
        )