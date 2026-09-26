from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from payroll.services import accrue_due_salary_liabilities


class Command(BaseCommand):
    help = "Move prepared, unpaid salary into Salaries Payable from the first day of the next month."
    # This financial task does not depend on image validation elsewhere in the
    # project, so an unrelated optional ImageField dependency must not stop a
    # first-of-month liability rollover.
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument(
            "--as-of",
            dest="as_of",
            help="Run the rollover as at YYYY-MM-DD. Defaults to today's Uganda-local date.",
        )

    def handle(self, *args, **options):
        as_of = timezone.localdate()
        if options.get("as_of"):
            try:
                as_of = date.fromisoformat(options["as_of"])
            except ValueError as exc:
                raise CommandError("--as-of must be a date in YYYY-MM-DD format.") from exc

        summary = accrue_due_salary_liabilities(as_of=as_of)
        self.stdout.write(
            self.style.SUCCESS(
                f"Salary liability rollover as at {as_of}: "
                f"{summary['accrued']} accrued, {summary['already_accrued']} already posted, "
                f"{summary['skipped']} not yet due or fully settled."
            )
        )
        for error in summary["errors"]:
            self.stderr.write(self.style.ERROR(error))
        if summary["errors"]:
            raise CommandError("One or more salary liabilities could not be posted.")
