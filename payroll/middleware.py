"""Low-frequency payroll safeguards used during normal application use."""

from django.db import OperationalError, ProgrammingError
from django.utils import timezone


_last_salary_liability_check = None


class AutoAccrueSalaryLiabilitiesMiddleware:
    """Move overdue prepared salary into payables once per local day.

    A scheduled management command remains available for unattended servers.
    This guard makes the first normal request on (or after) the first day of a
    new month catch up safely if that scheduled task was missed.  The payroll
    service is idempotent, so repeated server processes cannot create a second
    salary-accrual journal for the same payroll line.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._accrue_due_salaries_once_per_day()
        return self.get_response(request)

    def _accrue_due_salaries_once_per_day(self):
        global _last_salary_liability_check

        today = timezone.localdate()
        if _last_salary_liability_check == today:
            return

        try:
            from payroll.services import accrue_due_salary_liabilities

            accrue_due_salary_liabilities(as_of=today)
            _last_salary_liability_check = today
        except (OperationalError, ProgrammingError):
            # Requests can arrive while a new database is being migrated.
            # Leave the date unset so a later request retries normally.
            return
        except Exception:
            # Payroll remains accessible if an isolated legacy record needs
            # accounting attention. The payroll page and daily command expose
            # the underlying error without blocking every app request.
            return
