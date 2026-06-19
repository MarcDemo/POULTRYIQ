from django.db import models
from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator

# Create your models here.


class ExpenseCategory(models.Model):
    """
    Configurable categories for the unified expense ledger.
    Like: FEED, VET, LABOUR, UTILITIES, MAINTENANCE, BEDDING, TRANSPORT, OTHER
    """
    class ExpenseType(models.TextChoices):
        COST_OF_REVENUE = "COST_OF_REVENUE", "Cost of Revenue"
        DEPRECIATION = "DEPRECIATION", "Depreciation"
        MONTHLY_EXPENSES = "MONTHLY_EXPENSES", "Monthly Expenses"

    code = models.CharField(max_length=30, unique=True)  # e.g. FEED
    name = models.CharField(max_length=100)             # e.g. Feed & Nutrition
    expense_type = models.CharField(
        max_length=20,
        choices=ExpenseType.choices,
        default=ExpenseType.MONTHLY_EXPENSES,
        db_index=True,
        help_text="Categorize expense: Cost of Revenue, Depreciation, or Monthly Expenses"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class ExpenseTransaction(models.Model):
    """
    Single source of truth for all expenses (money-out).
    Raw record: do not silently overwrite after approval.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    expense_id = models.BigAutoField(primary_key=True)

    expense_date = models.DateField(db_index=True)
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name="expenses")

    description = models.CharField(max_length=255)  # e.g. "Maize bran purchase - 50kg bags"
    reference_no = models.CharField(max_length=80, blank=True, db_index=True)  # receipt/invoice number

    # Option, quantity fields (useful for feed/drugs even before full inventory linking)
    item_name = models.CharField(max_length=120, blank=True)  # e.g. "Maize bran"
    quantity = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.000"))]
    )
    unit = models.CharField(max_length=20, blank=True)  # kg, litres, pcs
    unit_cost = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    currency = models.CharField(max_length=10, default="UGX")
    total_amount = models.DecimalField(
        max_digits=14, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    supplier_name = models.CharField(max_length=150, blank=True)
    supplier_contact = models.CharField(max_length=80, blank=True)

    PAYMENT_CASH = "Cash"
    PAYMENT_MOBILE = "Mobile Money"
    PAYMENT_BANK = "Bank"
    PAYMENT_CHECK = "Check"
    PAYMENT_METHOD_CHOICES = [
        (PAYMENT_CASH, "Cash"),
        (PAYMENT_MOBILE, "Mobile Money"),
        (PAYMENT_BANK, "Bank"),
        (PAYMENT_CHECK, "Check"),
    ]
    payment_method = models.CharField(max_length=30, choices=PAYMENT_METHOD_CHOICES, default=PAYMENT_CASH, blank=True)

    # Period (fast monthly reporting)
    period_year = models.PositiveSmallIntegerField(db_index=True)
    period_month = models.PositiveSmallIntegerField(db_index=True)  # 1..12

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="expenses_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="expenses_approved"
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    rejection_reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-expense_date", "-expense_id"]
        indexes = [
            models.Index(fields=["category", "expense_date"]),
            models.Index(fields=["status", "expense_date"]),
            models.Index(fields=["period_year", "period_month"]),
        ]

    def __str__(self) -> str:
        return f"{self.expense_date} {self.category.code} {self.total_amount} {self.currency}"


class ExpenseAllocation(models.Model):
    """
    Allocates expenses to batches for profitability.
    batch = NULL means overhead/unallocated (still valid).
    """

    class Method(models.TextChoices):
        DIRECT = "DIRECT", "Direct Amount"
        PERCENT = "PERCENT", "Percentage"
        MANUAL = "MANUAL", "Manual Rule"

    allocation_id = models.BigAutoField(primary_key=True)
    expense = models.ForeignKey(ExpenseTransaction, on_delete=models.CASCADE, related_name="allocations")

    batch = models.ForeignKey(
        "poultry.PoultryBatch",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="expense_allocations",
    )

    method = models.CharField(max_length=10, choices=Method.choices, default=Method.DIRECT)

    percent = models.DecimalField(
        max_digits=6, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.000"))]
    )

    amount_allocated = models.DecimalField(
        max_digits=14, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    rationale = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["batch"]),
            models.Index(fields=["expense"]),
        ]

    def __str__(self) -> str:
        return f"{self.expense_id} -> {self.batch_id or 'OVERHEAD'}: {self.amount_allocated}"
