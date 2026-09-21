from django.db import models
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator


class SalaryPayment(models.Model):
    """
    A prepared payroll line for one employee and one payroll month.

    A line is deliberately separate from the money paid against it.  Preparing
    payroll is an operational step and must not create a journal entry.  The
    ``SalaryDisbursement`` records hold the audit trail for actual payments.
    """

    class Status(models.TextChoices):
        PREPARED = "PREPARED", "Prepared — unpaid"
        PART_PAID = "PART_PAID", "Part-paid"
        PAID = "PAID", "Paid"
        CANCELLED = "CANCELLED", "Cancelled / employee inactive"

    class PayBasis(models.TextChoices):
        MONTHLY = "MONTHLY", "Monthly salary"
        HOURLY = "HOURLY", "Hourly wage"

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
    lst_deduction = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Uganda Local Service Tax deducted for the selected payroll month.",
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
    pay_basis = models.CharField(
        max_length=10,
        choices=PayBasis.choices,
        default=PayBasis.MONTHLY,
        db_index=True,
    )
    hours_worked = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Approved hourly-work total used to calculate this payroll line.",
    )
    hourly_rate = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Hourly rate snapshot used for this payroll line.",
    )
    amount_paid = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Total of actual salary disbursements recorded against this payroll line.",
    )
    payment_date = models.DateField(null=True, blank=True, db_index=True)

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PREPARED, db_index=True
    )

    # These fields let payroll safely ask accounting to create the salary
    # payable only once.  The journal itself continues to live in accounting.
    liability_accrued_at = models.DateTimeField(null=True, blank=True)
    liability_entry_reference = models.CharField(max_length=80, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)

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
        indexes = [
            models.Index(fields=["period_month", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.employee} - {self.period_month}: {self.amount}"

    @property
    def total_due(self):
        """The net salary due for this line, normalised for older records."""
        return self.net_pay or self.amount

    @property
    def outstanding_amount(self):
        return max(self.total_due - self.amount_paid, Decimal("0.00"))

    @property
    def is_paid(self):
        return self.status == self.Status.PAID

    @property
    def is_cancelled(self):
        return self.status == self.Status.CANCELLED


class HourlyWorkEntry(models.Model):
    """A manager-entered hourly-work record that feeds one salary month.

    The rate is stored with each entry so a later change to an employee's
    onboarding profile cannot rewrite a prepared or historical payroll amount.
    Entries are linked to the payroll line once that line is prepared.
    """

    work_entry_id = models.BigAutoField(primary_key=True)
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="hourly_work_entries",
    )
    work_date = models.DateField(db_index=True)
    hours_worked = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    hourly_rate = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    notes = models.TextField(blank=True)
    salary_payment = models.ForeignKey(
        SalaryPayment,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="hourly_work_entries",
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="hourly_work_entries_recorded",
    )
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-work_date", "-work_entry_id"]
        indexes = [
            models.Index(fields=["employee", "work_date"]),
            models.Index(fields=["salary_payment", "work_date"]),
        ]

    def clean(self):
        if self.salary_payment and self.work_date.strftime("%Y-%m") != self.salary_payment.period_month:
            raise ValidationError(
                {"salary_payment": "Hourly work can only be allocated to its own payroll month."}
            )

    @property
    def gross_amount(self):
        return (self.hours_worked * self.hourly_rate).quantize(Decimal("0.01"))

    def __str__(self) -> str:
        return f"{self.employee} — {self.work_date}: {self.hours_worked} hours"


class SalaryDisbursement(models.Model):
    """One actual payment made against a prepared monthly salary line."""

    class PaymentMethod(models.TextChoices):
        CASH = "CASH", "Cash"
        BANK = "BANK", "Bank"
        MOBILE_MONEY = "MOBILE_MONEY", "Mobile Money"
        CHEQUE = "CHEQUE", "Cheque"
        OTHER = "OTHER", "Other"

    disbursement_id = models.BigAutoField(primary_key=True)
    salary = models.ForeignKey(
        SalaryPayment,
        on_delete=models.PROTECT,
        related_name="disbursements",
    )
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    payment_reference = models.CharField(
        max_length=120,
        help_text="Receipt, bank, mobile-money, or batch reference supplied when the payment is made.",
    )
    paid_on = models.DateField(db_index=True)
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="salary_disbursements_recorded",
    )
    accounting_entry_reference = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_on", "-disbursement_id"]
        indexes = [
            models.Index(fields=["salary", "paid_on"]),
            models.Index(fields=["payment_reference"]),
        ]

    def __str__(self) -> str:
        return f"{self.salary} — {self.amount} on {self.paid_on}"


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
    bonus_name = models.CharField(max_length=120, default="Bonus")
    reason = models.TextField(blank=True)
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
