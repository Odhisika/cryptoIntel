from django.core.management.base import BaseCommand

from core.backtest import build_backtest_report, HORIZONS_DAYS
from core.scoring.tiers import RewardTier, TIER_LABELS


class Command(BaseCommand):
    help = (
        "Run the historical score accuracy backtest and print win rate, "
        "average return, and annualized Sharpe per tier and horizon."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--model-version", default=None,
            help="Restrict to a single 10X model version (recommended).",
        )

    def handle(self, *args, **options):
        report = build_backtest_report(model_version=options["model_version"])

        self.stdout.write(self.style.MIGRATE_HEADING("Backtest Accuracy"))
        if options["model_version"]:
            self.stdout.write(f"model_version: {options['model_version']}")
        self.stdout.write(report["headline"])
        self.stdout.write("")

        header = f"{'Tier':<16}" + "".join(
            f"{str(h):<7}{'n':<5}{'win':<8}{'avg':<10}{'sharpe':<9}" for h in HORIZONS_DAYS
        )
        self.stdout.write(header)

        for tier in RewardTier:
            row = f"{TIER_LABELS[tier]:<16}"
            for h in HORIZONS_DAYS:
                m = report["horizons"][str(h)]["tiers"][tier.value]
                win = f"{float(m['win_rate']):.0%}" if m["win_rate"] is not None else "-"
                avg = f"{float(m['avg_return']):.1%}" if m["avg_return"] is not None else "-"
                sharpe = f"{float(m['sharpe']):.2f}" if m["sharpe"] is not None else "-"
                row += f"{h:<7}{m['count']:<5}{win:<8}{avg:<10}{sharpe:<9}"
            self.stdout.write(row)

        self.stdout.write("")
        for h in HORIZONS_DAYS:
            s = report["horizons"][str(h)]["summary"]
            win = "-" if s["win_rate"] is None else f"{float(s['win_rate']):.0%}"
            avg = "-" if s["avg_return"] is None else f"{float(s['avg_return']):.1%}"
            sharpe = "-" if s["sharpe"] is None else f"{float(s['sharpe']):.2f}"
            self.stdout.write(f"All tiers, {h}d: n={s['count']} win={win} avg={avg} sharpe={sharpe}")
