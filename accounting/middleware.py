from django.db import OperationalError, ProgrammingError
from django.utils import timezone


_last_checked_date = None


class AutoPostMonthlyDepreciationMiddleware:
    """
    Posts depreciation for completed months during normal app use.

    The journal reference is unique per asset/month, so repeated checks are safe.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._post_due_depreciation_once_per_day()
        return self.get_response(request)

    def _post_due_depreciation_once_per_day(self):
        global _last_checked_date

        today = timezone.localdate()
        if _last_checked_date == today:
            return

        try:
            from accounting.services import record_due_monthly_depreciation

            record_due_monthly_depreciation(as_of_date=today)
            _last_checked_date = today
        except (OperationalError, ProgrammingError):
            # The database may not be ready while migrations are running.
            return
        except Exception:
            return
