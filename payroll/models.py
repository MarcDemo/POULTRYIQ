from django.db import models
from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator


class SalaryPayment(models.Model):
    """
    Records salary payments to employees.
    These are recorded as monthly_expenses in the accounting system.
    """

    class Status(models.TextChoices):
        PAID = "PAID", "Paid"
        PENDING = "PENDING", "Pending"

    salary_id = models.BigAutoField(primary_key=True)

    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="salary_payments"
    )
    # Stored as "YYYY-MM" matching the HTML <input type="month"> value format
    period_month = models.CharField(max_length=7, db_index=True)  # e.g. "2026-02"

    amount = models.DecimalField(
        max_digits=14, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="salaries_recorded"
    )
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_month", "employee__username"]

    def __str__(self) -> str:
        return f"{self.employee} - {self.period_month}: {self.amount}"
