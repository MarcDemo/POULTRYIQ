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
    gross_salary = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    paye_tax = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    nssf_employee = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    nssf_employer = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    advances_deducted = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    bonus_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    net_pay = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    payment_date = models.DateField(null=True, blank=True, db_index=True)

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
    edit_reason = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_month", "employee__username"]
        unique_together = ("employee", "period_month")

    def __str__(self) -> str:
        return f"{self.employee} - {self.period_month}: {self.amount}"


class SalaryBonus(models.Model):
    bonus_id = models.BigAutoField(primary_key=True)
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="salary_bonuses",
    )
    period_month = models.CharField(max_length=7, db_index=True)
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    reason = models.TextField()
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="salary_bonuses_granted",
    )
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_month", "employee__username", "-granted_at"]

    def __str__(self) -> str:
        return f"{self.employee} - {self.period_month}: bonus {self.amount}"


class SalaryPaymentEditLog(models.Model):
    log_id = models.BigAutoField(primary_key=True)
    salary = models.ForeignKey(SalaryPayment, on_delete=models.CASCADE, related_name="edit_logs")
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="salary_payment_edits",
    )
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-log_id"]

    def __str__(self) -> str:
        return f"{self.salary} edited by {self.edited_by}"
