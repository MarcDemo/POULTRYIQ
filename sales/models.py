from django.db import models
from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator

# Create your models here.
# sales/models.py



class Customer(models.Model):
    customer_id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=150, unique=True)
    contact_person = models.CharField(max_length=120, blank=True)
    phone_number = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)

    # Credit controls
    allow_credit = models.BooleanField(default=True)
    credit_limit = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    credit_days = models.PositiveSmallIntegerField(default=0)  # e.g. 7, 14, 30

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class SaleInvoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ISSUED = "ISSUED", "Issued"
        PAID = "PAID", "Paid"
        CANCELLED = "CANCELLED", "Cancelled"

    class DeliveryStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        DELIVERED = "DELIVERED", "Delivered"
        CANCELLED = "CANCELLED", "Cancelled"

    invoice_id = models.BigAutoField(primary_key=True)
    invoice_no = models.CharField(max_length=60, unique=True, db_index=True)  # e.g. INV-2026-0001

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="invoices")

    invoice_date = models.DateField(db_index=True)
    due_date = models.DateField(null=True, blank=True, db_index=True)

    currency = models.CharField(max_length=10, default="UGX")

    # Totals
    subtotal = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    discount_amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    total_amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True)
    delivery_status = models.CharField(
        max_length=12,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.PENDING,
        db_index=True,
    )

    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="invoices_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-invoice_date", "-invoice_id"]
        indexes = [
            models.Index(fields=["customer", "invoice_date"]),
            models.Index(fields=["status", "invoice_date"]),
        ]

    def __str__(self) -> str:
        return self.invoice_no


class SaleItem(models.Model):
    """
    Line item for a SaleInvoice.
    For batch-level profitability: link each line to one batch where applicable.
    If sale is mixed batches, we will create multiple line items (one per batch).
    """
    item_id = models.BigAutoField(primary_key=True)
    invoice = models.ForeignKey(SaleInvoice, on_delete=models.CASCADE, related_name="items")

    # Optional: link revenue to a specific batch for profitability
    batch = models.ForeignKey(
        "poultry.PoultryBatch", on_delete=models.PROTECT, null=True, blank=True, related_name="sale_items"
    )

    product_name = models.CharField(max_length=120)  # e.g. Eggs, Off-layers, Manure
    quantity = models.DecimalField(
        max_digits=12, decimal_places=3,
        validators=[MinValueValidator(Decimal("0.000"))]
    )
    unit = models.CharField(max_length=20, default="trays")  # trays, birds, kg, bags
    unit_price = models.DecimalField(
        max_digits=14, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    line_total = models.DecimalField(
        max_digits=14, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    class Meta:
        indexes = [
            models.Index(fields=["batch"]),
            models.Index(fields=["invoice"]),
        ]

    def __str__(self) -> str:
        return f"{self.invoice.invoice_no} - {self.product_name}"


class ReceivableLedger(models.Model):
    """
    One receivable record per invoice (simple and lean).
    Later, we can evolve into a true ledger with entries, if needed.
    """
    receivable_id = models.BigAutoField(primary_key=True)
    invoice = models.OneToOneField(SaleInvoice, on_delete=models.CASCADE, related_name="receivable")

    amount_due = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    amount_paid = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    balance = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    last_payment_date = models.DateField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["balance"]),
        ]

    def __str__(self) -> str:
        return f"{self.invoice.invoice_no} balance {self.balance}"


class CustomerPayment(models.Model):
    """
    Payments received from customers (supports partial payments).
    """
    class Method(models.TextChoices):
        CASH = "CASH", "Cash"
        MOMO = "MOMO", "Mobile Money"
        BANK = "BANK", "Bank"
        OTHER = "OTHER", "Other"

    payment_id = models.BigAutoField(primary_key=True)
    invoice = models.ForeignKey(SaleInvoice, on_delete=models.PROTECT, related_name="payments")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="payments")

    payment_date = models.DateField(db_index=True)
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.CASH)

    amount = models.DecimalField(
        max_digits=14, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    reference = models.CharField(max_length=80, blank=True)  # momo ref, bank slip, receipt no
    notes = models.TextField(blank=True)

    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="customer_payments_received"
    )
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payment_date", "-payment_id"]
        indexes = [
            models.Index(fields=["customer", "payment_date"]),
            models.Index(fields=["invoice", "payment_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.customer.name} {self.amount} on {self.payment_date}"