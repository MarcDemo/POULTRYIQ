from django.core.management.base import BaseCommand, CommandError

from accounting.services import record_monthly_depreciation


class Command(BaseCommand):
    help = "Post monthly straight-line depreciation for fixed assets."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, help="Posting year. Defaults to the current year.")
        parser.add_argument("--month", type=int, help="Posting month, 1-12. Defaults to the current month.")

    def handle(self, *args, **options):
        try:
            summary = record_monthly_depreciation(
                year=options.get("year"),
                month=options.get("month"),
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Posted {summary['posted']} depreciation entries for "
                f"{summary['month']:02d}/{summary['year']} totalling {summary['total_amount']}."
            )
        )
        if summary["skipped"]:
            self.stdout.write(f"Skipped {summary['skipped']} assets.")
        for error in summary["errors"]:
            self.stdout.write(self.style.WARNING(error))
