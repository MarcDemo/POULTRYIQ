from django.db import models
from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator

# Create your models here.


class StaffProfile(models.Model):
    class EmploymentStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        ON_LEAVE = "ON_LEAVE", "On Leave"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff_profile")
    profile_photo = models.ImageField(upload_to="staff_profiles/", blank=True)
    age = models.PositiveSmallIntegerField(null=True, blank=True)
    job_title = models.CharField(max_length=100, blank=True)
    employee_number = models.CharField(max_length=40, blank=True, db_index=True)
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
    pay_nssf = models.BooleanField(default=True)
    pay_paye = models.BooleanField(default=True)
    employment_status = models.CharField(
        max_length=12,
        choices=EmploymentStatus.choices,
        default=EmploymentStatus.ACTIVE,
        db_index=True,
    )
    hire_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__first_name", "user__username"]
        indexes = [
            models.Index(fields=["employment_status"], name="hr_staffpro_employm_9cf0ba_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user.display_name} profile"



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
        GENERAL = "GENERAL", "General Welfare"

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

    def __str__(self) -> str:
        return f"{self.worker} - {self.get_request_type_display()} ({self.get_status_display()})"
