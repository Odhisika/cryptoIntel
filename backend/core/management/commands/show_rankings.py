from decimal import Decimal

from django.core.management.base import BaseCommand

from core.models import ScoreSnapshot
from core.scoring import momentum, potential_10x, risk, undervaluation
from core.scoring.ranking import rank_by_model

# Current version per model — kept here (not duplicated as a hardcoded
# CLI default) so this command never silently points at a stale version
# after a score model is bumped.
CURRENT_VERSIONS = {
    ScoreSnapshot.ModelName.TEN_X_POTENTIAL: potential_10x.MODEL_VERSION,
    ScoreSnapshot.ModelName.UNDERVALUATION: undervaluation.MODEL_VERSION,
    ScoreSnapshot.ModelName.MOMENTUM: momentum.MODEL_VERSION,
    ScoreSnapshot.ModelName.RISK: risk.MODEL_VERSION,
}


class Command(BaseCommand):
    help = "Print the current ranking for a given score model+version."

    def add_arguments(self, parser):
        parser.add_argument("model_name", choices=[c[0] for c in ScoreSnapshot.ModelName.choices])
        parser.add_argument(
            "--model-version", default=None, help="Defaults to that model's current version if omitted."
        )
        parser.add_argument("--min-confidence", type=str, default="0")
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options):
        model_version = options["model_version"] or CURRENT_VERSIONS[options["model_name"]]

        ranked = rank_by_model(
            options["model_name"],
            model_version,
            min_data_confidence=Decimal(options["min_confidence"]),
            limit=options["limit"],
        )

        if not ranked:
            self.stdout.write(
                self.style.WARNING(f"No scores found for {options['model_name']} {model_version}.")
            )
            return

        self.stdout.write(f"{'Rank':<6}{'Symbol':<10}{'Score':<10}{'Confidence':<12}")
        for r in ranked:
            self.stdout.write(
                f"{r.rank:<6}{r.symbol.upper():<10}{str(r.score):<10}{f'{float(r.data_confidence):.0%}':<12}"
            )
