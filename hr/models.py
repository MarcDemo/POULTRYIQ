from django.db import models
from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta

# Create your models here.


class StaffProfile(models.Model):
    class EmploymentStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        ON_LEAVE = "ON_LEAVE", "On Leave"

    class PayBasis(models.TextChoices):
        MONTHLY = "MONTHLY", "Monthly salary"
        HOURLY = "HOURLY", "Hourly wage"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff_profile")
    profile_photo = models.ImageField(upload_to="staff_profiles/", blank=True)
    # Kept only so historic profile data is not discarded when the visible field
    # changes from age to date of birth. New onboarding never writes this field.
    legacy_age = models.PositiveSmallIntegerField(null=True, blank=True, editable=False)
    date_of_birth = models.DateField(null=True, blank=True)
    job_title = models.CharField(max_length=100, blank=True)
    employee_number = models.CharField(max_length=40, blank=True, unique=True, editable=False)
    tin_number = models.CharField(max_length=50, blank=True)
    nssf_number = models.CharField(max_length=50, blank=True)
    national_id = models.CharField(max_length=50, blank=True)
    next_of_kin_name = models.CharField(max_length=150, blank=True)
    next_of_kin_contact = models.CharField(max_length=50, blank=True)
    physical_address = models.CharField(max_length=200, blank=True)
    emergency_contact = models.CharField(max_length=50, blank=True)
    monthly_salary = models.DecimalField(
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
    hourly_rate = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    pay_nssf = models.BooleanField(default=True)
    pay_paye = models.BooleanField(default=True)
    # LST is only deducted when the manager confirms this employee is eligible.
    pay_lst = models.BooleanField(default=False)
    lst_local_government = models.CharField(max_length=120, blank=True)
    employment_status = models.CharField(
        max_length=12,
        choices=EmploymentStatus.choices,
        default=EmploymentStatus.ACTIVE,
        db_index=True,
    )
    hire_date = models.DateField(null=True, blank=True)
    contract_start_date = models.DateField(null=True, blank=True)
    contract_end_date = models.DateField(null=True, blank=True, db_index=True)
    contract_notes = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__first_name", "user__username"]
        indexes = [
            models.Index(fields=["employment_status"], name="hr_staffpro_employm_9cf0ba_idx"),
            models.Index(fields=["pay_basis", "employment_status"], name="hr_staffpro_pay_bas_17fd6a_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user.display_name} profile"

    @property
    def contract_is_expired(self) -> bool:
        return bool(self.contract_end_date and self.contract_end_date < timezone.localdate())

    @property
    def contract_expiring_soon(self) -> bool:
        if not self.contract_end_date or self.contract_is_expired:
            return False
        return self.contract_end_date <= timezone.localdate() + timedelta(days=30)

    @classmethod
    def _next_employee_number(cls) -> str:
        """Return the next readable staff ID for the current onboarding year."""
        year = timezone.localdate().year
        prefix = f"EMP-{year}-"
        highest = 0
        for value in cls.objects.filter(employee_number__startswith=prefix).values_list(
            "employee_number", flat=True
        ):
            suffix = value.removeprefix(prefix)
            if suffix.isdigit():
                highest = max(highest, int(suffix))
        return f"{prefix}{highest + 1:05d}"

    def clean(self):
        errors = {}
        if self.date_of_birth and self.date_of_birth > timezone.localdate():
            errors["date_of_birth"] = "Date of birth cannot be in the future."
        if self.contract_start_date and self.contract_end_date and self.contract_end_date < self.contract_start_date:
            errors["contract_end_date"] = "Contract expiry cannot be before the contract start date."
        if self.pay_basis == self.PayBasis.HOURLY and self.hourly_rate <= Decimal("0.00"):
            errors["hourly_rate"] = "An hourly employee needs an hourly wage greater than zero."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Employee IDs are issued once by the system and cannot be changed later.
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values_list("employee_number", flat=True).first()
            if original:
                self.employee_number = original
        if not self.employee_number:
            self.employee_number = self._next_employee_number()
        return super().save(*args, **kwargs)


class ContractRenewal(models.Model):
    """An immutable historical record whenever a staff member's contract is renewed."""

    renewal_id = models.BigAutoField(primary_key=True)
    staff_profile = models.ForeignKey(
        StaffProfile,
        on_delete=models.CASCADE,
        related_name="contract_renewals",
    )
    previous_contract_start_date = models.DateField(null=True, blank=True)
    previous_contract_end_date = models.DateField(null=True, blank=True)
    renewed_contract_start_date = models.DateField()
    renewed_contract_end_date = models.DateField()
    notes = models.TextField(blank=True)
    renewal_document = models.FileField(upload_to="staff_contracts/", blank=True)
    renewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="contract_renewals_recorded",
    )
    renewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-renewed_contract_end_date", "-renewal_id"]
        indexes = [models.Index(fields=["staff_profile", "renewed_contract_end_date"])]

    def clean(self):
        if self.renewed_contract_end_date < self.renewed_contract_start_date:
            raise ValidationError({"renewed_contract_end_date": "Renewal expiry cannot be before its start date."})

    def __str__(self) -> str:
        return f"{self.staff_profile.employee_number} renewal to {self.renewed_contract_end_date}"


class EmployeeDocument(models.Model):
    class DocumentType(models.TextChoices):
        NATIONAL_ID = "NATIONAL_ID", "National ID"
        CONTRACT = "CONTRACT", "Employment contract"
        TAX = "TAX", "Tax document"
        NSSF = "NSSF", "NSSF document"
        QUALIFICATION = "QUALIFICATION", "Qualification / certificate"
        OTHER = "OTHER", "Other"

    document_id = models.BigAutoField(primary_key=True)
    staff_profile = models.ForeignKey(
        StaffProfile,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    title = models.CharField(max_length=150)
    document = models.FileField(upload_to="employee_documents/")
    expiry_date = models.DateField(null=True, blank=True, db_index=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="employee_documents_uploaded",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at", "-document_id"]
        indexes = [models.Index(fields=["staff_profile", "document_type"])]

    @property
    def is_expired(self) -> bool:
        return bool(self.expiry_date and self.expiry_date < timezone.localdate())

    def __str__(self) -> str:
        return f"{self.staff_profile.employee_number} - {self.title}"


class UnpaidAbsence(models.Model):
    """A manager-recorded full day away from work without pay."""

    absence_id = models.BigAutoField(primary_key=True)
    staff_profile = models.ForeignKey(
        StaffProfile,
        on_delete=models.CASCADE,
        related_name="unpaid_absences",
    )
    absence_date = models.DateField(db_index=True)
    reason = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="unpaid_absences_recorded",
    )
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-absence_date", "-absence_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["staff_profile", "absence_date"],
                name="hr_one_unpaid_absence_per_day",
            )
        ]
        indexes = [models.Index(fields=["staff_profile", "absence_date"])]

    def clean(self):
        if self.absence_date > timezone.localdate():
            raise ValidationError({"absence_date": "An unpaid absence cannot be recorded for a future date."})

    def __str__(self) -> str:
        return f"{self.staff_profile.employee_number} away without pay on {self.absence_date}"



class Worker(models.Model):
    """
    Farm worker profile for wage tracking and future HR analytics.
    """
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"

    worker_id = models.BigAutoField(primary_key=True)
    full_name = models.CharField(max_length=150)
    national_id = models.CharField(max_length=30, blank=True)  # optional
    phone_number = models.CharField(max_length=30, blank=True)

    job_title = models.CharField(max_length=100, blank=True)   # e.g. Attendant, Supervisor
    start_date = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="workers_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["full_name"]
        indexes = [
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return self.full_name


class Attendance(models.Model):
    """
    Daily attendance record.
    """
    class AttendanceStatus(models.TextChoices):
        PRESENT = "PRESENT", "Present"
        ABSENT = "ABSENT", "Absent"
        OFF = "OFF", "Off / Leave"

    attendance_id = models.BigAutoField(primary_key=True)
    worker = models.ForeignKey(Worker, on_delete=models.PROTECT, related_name="attendance_records")

    work_date = models.DateField(db_index=True)
    status = models.CharField(max_length=10, choices=AttendanceStatus.choices, default=AttendanceStatus.PRESENT)

    hours_worked = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    notes = models.TextField(blank=True)

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="attendance_recorded"
    )
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-work_date", "-attendance_id"]
        unique_together = ("worker", "work_date")
        indexes = [
            models.Index(fields=["worker", "work_date"]),
            models.Index(fields=["work_date", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.worker.full_name} - {self.work_date} ({self.status})"


class WagePayment(models.Model):
    """
    Records payments made to workers.
    Batch is optional: wages can be allocated to batches using ExpenseAllocation (recommended),
    but direct batch tagging is allowed for special cases.
    """
    wage_id = models.BigAutoField(primary_key=True)
    worker = models.ForeignKey(Worker, on_delete=models.PROTECT, related_name="wage_payments")

    payment_date = models.DateField(db_index=True)

    amount = models.DecimalField(
        max_digits=14, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    currency = models.CharField(max_length=10, default="UGX")

    # Optional direct batch tagging (most wage costs are better allocated via ExpenseAllocation)
    batch = models.ForeignKey(
        "poultry.PoultryBatch", on_delete=models.PROTECT, null=True, blank=True, related_name="wage_payments"
    )

    # Period tagging for reporting
    period_year = models.PositiveSmallIntegerField(db_index=True)
    period_month = models.PositiveSmallIntegerField(db_index=True)  # 1..12

    reference = models.CharField(max_length=80, blank=True)  # receipt no / momo ref / voucher
    notes = models.TextField(blank=True)

    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="wages_paid"
    )
    paid_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payment_date", "-wage_id"]
        indexes = [
            models.Index(fields=["worker", "payment_date"]),
            models.Index(fields=["period_year", "period_month"]),
        ]

    def __str__(self) -> str:
        return f"{self.worker.full_name} {self.amount} {self.currency} on {self.payment_date}"


class WelfareRequest(models.Model):
    """
    Worker welfare request with supervisor screening before manager decision.
    """
    class RequestType(models.TextChoices):
        LEAVE = "LEAVE", "Leave"
        SALARY_ADVANCE = "SALARY_ADVANCE", "Salary Advance"

    class AdvancePaymentMethod(models.TextChoices):
        CASH = "CASH", "Cash"
        BANK = "BANK", "Bank"
        MOBILE_MONEY = "MOBILE_MONEY", "Mobile Money"
        CHEQUE = "CHEQUE", "Cheque"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Submitted to Supervisor"
        SUPERVISOR_SUBMITTED = "SUPERVISOR_SUBMITTED", "Submitted to Manager"
        SUPERVISOR_APPROVED = "SUPERVISOR_APPROVED", "Sent to Manager"
        SUPERVISOR_REJECTED = "SUPERVISOR_REJECTED", "Rejected by Supervisor"
        MANAGER_APPROVED = "MANAGER_APPROVED", "Approved by Manager"
        MANAGER_REJECTED = "MANAGER_REJECTED", "Rejected by Manager"

    request_id = models.BigAutoField(primary_key=True)
    worker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="welfare_requests",
    )
    request_type = models.CharField(max_length=20, choices=RequestType.choices, db_index=True)
    title = models.CharField(max_length=120)
    details = models.TextField()

    leave_start = models.DateField(null=True, blank=True)
    leave_end = models.DateField(null=True, blank=True)
    advance_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    advance_period_start = models.DateField(null=True, blank=True, db_index=True)
    advance_period_end = models.DateField(null=True, blank=True, db_index=True)
    # Approval and issuance are intentionally different events.  An approved
    # request is not a receivable until money has actually been handed over.
    advance_disbursed_on = models.DateField(null=True, blank=True, db_index=True)
    advance_payment_method = models.CharField(
        max_length=20,
        choices=AdvancePaymentMethod.choices,
        blank=True,
    )
    advance_payment_reference = models.CharField(max_length=120, blank=True)
    advance_journal_reference = models.CharField(max_length=80, blank=True)
    advance_disbursed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="welfare_advances_disbursed",
    )
    currency = models.CharField(max_length=10, default="UGX")

    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.SUBMITTED,
        db_index=True,
    )
    supervisor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="welfare_requests_supervised",
    )
    supervisor_notes = models.TextField(blank=True)
    supervisor_reviewed_at = models.DateTimeField(null=True, blank=True)

    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="welfare_requests_managed",
    )
    manager_notes = models.TextField(blank=True)
    manager_reviewed_at = models.DateTimeField(null=True, blank=True)
    salary_payment = models.ForeignKey(
        "payroll.SalaryPayment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deducted_advances",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-request_id"]
        indexes = [
            models.Index(fields=["worker", "status"]),
            models.Index(fields=["request_type", "status"]),
            models.Index(fields=["created_at"]),
        ]

    def clean(self):
        errors = {}
        if self.request_type == self.RequestType.SALARY_ADVANCE:
            if self.advance_period_start and self.advance_period_end and self.advance_period_end < self.advance_period_start:
                errors["advance_period_end"] = "Recovery end month cannot be before the recovery start month."
            disbursement_details = [
                self.advance_payment_method,
                self.advance_payment_reference,
                self.advance_disbursed_by_id,
            ]
            if self.advance_disbursed_on and not all(disbursement_details):
                errors["advance_disbursed_on"] = (
                    "Issued advances need a payment method, reference, and the person who recorded the issue."
                )
            if not self.advance_disbursed_on and any(disbursement_details):
                errors["advance_disbursed_on"] = "Enter an issue date before recording payment details."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.worker} - {self.get_request_type_display()} ({self.get_status_display()})"

    @property
    def is_advance_disbursed(self) -> bool:
        return bool(self.advance_disbursed_on)

    @property
    def advance_allocated_amount(self):
        """Amount already assigned to one or more prepared salary months.

        The legacy ``salary_payment`` link represented a whole advance being
        recovered from one month.  New records use allocation rows so a single
        advance can be recovered over several salary months.
        """
        allocations = getattr(self, "_prefetched_advance_allocations", None)
        if allocations is None:
            allocations = self.salary_advance_allocations.all()
        allocated = sum((allocation.amount for allocation in allocations), Decimal("0.00"))
        if allocated == Decimal("0.00") and self.salary_payment_id:
            return self.advance_amount or Decimal("0.00")
        return allocated

    @property
    def recovery_allocations(self):
        """Allocation rows, using the profile/register prefetch when present."""
        prefetched = getattr(self, "_prefetched_advance_allocations", None)
        if prefetched is not None:
            return prefetched
        return self.salary_advance_allocations.select_related("salary_payment", "allocated_by").all()

    @property
    def advance_outstanding_amount(self):
        return max((self.advance_amount or Decimal("0.00")) - self.advance_allocated_amount, Decimal("0.00"))


class SalaryAdvanceAllocation(models.Model):
    """The portion of a disbursed advance recovered from a salary month."""

    allocation_id = models.BigAutoField(primary_key=True)
    advance = models.ForeignKey(
        WelfareRequest,
        on_delete=models.PROTECT,
        related_name="salary_advance_allocations",
    )
    salary_payment = models.ForeignKey(
        "payroll.SalaryPayment",
        on_delete=models.PROTECT,
        related_name="salary_advance_allocations",
    )
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    allocated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="salary_advance_allocations_recorded",
    )
    allocated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["salary_payment__period_month", "allocation_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["advance", "salary_payment"],
                name="hr_one_advance_allocation_per_salary_month",
            ),
        ]
        indexes = [
            models.Index(fields=["advance", "salary_payment"]),
        ]

    def clean(self):
        errors = {}
        if self.advance.request_type != WelfareRequest.RequestType.SALARY_ADVANCE:
            errors["advance"] = "Only salary-advance requests can be allocated to payroll."
        if self.advance.worker_id != self.salary_payment.employee_id:
            errors["salary_payment"] = "An advance can only be recovered from the same employee's salary."
        if not self.advance.is_advance_disbursed:
            errors["advance"] = "A salary advance must be issued before it can be recovered."
        if self.amount and self.advance_id:
            previous_total = self.advance.salary_advance_allocations.exclude(pk=self.pk).aggregate(
                total=models.Sum("amount")
            )["total"] or Decimal("0.00")
            if previous_total + self.amount > (self.advance.advance_amount or Decimal("0.00")):
                errors["amount"] = "Recovery allocation cannot exceed the amount of the issued advance."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.advance_id} recovered from {self.salary_payment.period_month}: {self.amount}"
