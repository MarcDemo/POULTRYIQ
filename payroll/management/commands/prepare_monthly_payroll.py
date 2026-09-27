from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from payroll.services import accrue_due_salary_liabilities, prepare_monthly_payroll, validate_period_month


class Command(BaseCommand):
    help = "Prepare a payroll month and catch up any salaries that are already due as liabilities."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument(
            "--month",
            dest="period_month",
            help="Payroll month in YYYY-MM format. Defaults to the current month.",
        )

    def handle(self, *args, **options):
        period_month = options.get("period_month") or timezone.localdate().strftime("%Y-%m")
        try:
            validate_period_month(period_month)
            summary = prepare_monthly_payroll(period_month)
            rollover = accrue_due_salary_liabilities(as_of=timezone.localdate())
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Monthly payroll prepared. "
                f"Month: {summary['period_month']}. "
                f"Records ready: {summary['prepared']}. "
                f"New records: {summary['created']}. "
                f"Already-paid skipped: {summary['skipped_paid']}. "
                f"Overdue liabilities posted: {rollover['accrued']}."
            )
        )
        for error in rollover["errors"]:
            self.stderr.write(self.style.WARNING(error))
