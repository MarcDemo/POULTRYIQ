from django.db import models
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db.models.functions import Lower

# Create your models here.


class Supplier(models.Model):
    class PaymentMethod(models.TextChoices):
        CASH = "Cash", "Cash"
        MOBILE_MONEY = "Mobile Money", "MoMo"
        BANK = "Bank", "Bank"
        CREDIT = "Credit", "Credit"

    name = models.CharField(max_length=150, unique=True)
    tin_number = models.CharField(max_length=50, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    location = models.CharField(max_length=150, blank=True)
    product = models.CharField(max_length=150, blank=True)
    supplied_accounts = models.ManyToManyField(
        "accounting.ChartOfAccount",
        blank=True,
        related_name="suppliers",
    )
    supplied_products = models.ManyToManyField(
        "SupplierProduct",
        blank=True,
        related_name="suppliers",
    )
    other_supplied_products = models.TextField(blank=True)
    preferred_payment_method = models.CharField(
        max_length=30,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH,
    )
    bank_account_number = models.CharField(max_length=80, blank=True)
    momo_receiving_number = models.CharField(max_length=30, blank=True)
    credit_repayment_plan = models.TextField(blank=True)
    credit_paid_upfront = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))],
    )
    credit_grace_period_days = models.PositiveIntegerField(null=True, blank=True)
    credit_period = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class SupplierProduct(models.Model):
    account = models.ForeignKey(
        "accounting.ChartOfAccount",
        on_delete=models.PROTECT,
        related_name="supplier_products",
    )
    name = models.CharField(max_length=120)
    unit = models.CharField(max_length=20, default="unit")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["account__account_name", "name"]
        unique_together = ("account", "name")
        constraints = [
            models.UniqueConstraint(Lower("name"), name="unique_supplier_product_name_ci"),
        ]

    def __str__(self):
        return f"{self.name} ({self.account.account_name})"

    def clean(self):
        duplicate = SupplierProduct.objects.filter(name__iexact=self.name)
        if self.pk:
            duplicate = duplicate.exclude(pk=self.pk)
        if duplicate.exists():
            raise ValidationError({"name": "A supplier product with this name already exists."})



class Store(models.Model):
    """
    Physical/Logical store e.g. Feed Store, Drug Store, General Store.
    """
    name = models.CharField(max_length=100, unique=True)
    location_note = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name


class ItemCategory(models.Model):
    """
    FEED, DRUG, BEDDING, CONSUMABLE, etc.
    """
    code = models.CharField(max_length=30, unique=True)  # e.g. FEED
    name = models.CharField(max_length=100)

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class Item(models.Model):
    """
    Inventory master item e.g. Maize Bran, Concentrate, Vaccine X.
    """
    name = models.CharField(max_length=120, unique=True)
    category = models.ForeignKey(ItemCategory, on_delete=models.PROTECT, related_name="items")
    unit = models.CharField(max_length=20, default="kg")  # kg, litres, pcs, trays etc.
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name


class InventoryTransaction(models.Model):
    class TxType(models.TextChoices):
        IN_ = "IN", "Stock In"
        OUT = "OUT", "Stock Out"
        ADJUST = "ADJUST", "Adjustment"

    class PaymentMethod(models.TextChoices):
        CASH = "Cash", "Cash"
        MOBILE_MONEY = "Mobile Money", "MoMo"
        BANK = "Bank", "Bank"
        CREDIT = "Credit", "Credit"

    tx_id = models.BigAutoField(primary_key=True)
    tx_date = models.DateField(db_index=True)
    tx_type = models.CharField(max_length=10, choices=TxType.choices, db_index=True)

    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="transactions")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="transactions")

    quantity = models.DecimalField(
        max_digits=12, decimal_places=3,
        validators=[MinValueValidator(Decimal("0.000"))]
    )

    supplier_name = models.CharField(max_length=150, blank=True)
    unit_price = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    expiry_date = models.DateField(null=True, blank=True)
    payment_method = models.CharField(max_length=30, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    bank_account_number = models.CharField(max_length=80, blank=True)
    momo_receiving_number = models.CharField(max_length=30, blank=True)
    credit_repayment_plan = models.TextField(blank=True)
    credit_paid_upfront = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))],
    )
    credit_grace_period_days = models.PositiveIntegerField(null=True, blank=True)
    credit_period = models.CharField(max_length=100, blank=True)
    credit_due_date = models.DateField(null=True, blank=True)

    # Link stock-out to a batch (optional; replace with your actual model path)
    batch = models.ForeignKey(
        "poultry.PoultryBatch", on_delete=models.PROTECT, null=True, blank=True,
        related_name="inventory_transactions"
    )

    reference = models.CharField(max_length=120, blank=True)  # receipt no, expense id, notes
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="inventory_created")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.tx_date} {self.tx_type} {self.item.name} {self.quantity}{self.item.unit}"


class ReorderRule(models.Model):
    """
    Low stock alert thresholds per item per store.
    """
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="reorder_rules")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="reorder_rules")

    minimum_level = models.DecimalField(
        max_digits=12, decimal_places=3,
        validators=[MinValueValidator(Decimal("0.000"))]
    )
    reorder_level = models.DecimalField(
        max_digits=12, decimal_places=3,
        validators=[MinValueValidator(Decimal("0.000"))]
    )

    alerts_enabled = models.BooleanField(default=True)

    class Meta:
        unique_together = ("store", "item")

    def __str__(self) -> str:
        return f"{self.store} - {self.item} (min {self.minimum_level}{self.item.unit})"


class InventoryRequisition(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Submitted to Manager"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    requisition_id = models.BigAutoField(primary_key=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="inventory_requisitions",
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, null=True, blank=True, related_name="requisitions")
    item_name = models.CharField(max_length=120, blank=True)
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.000"))],
    )
    unit_price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    unit = models.CharField(max_length=20, default="kg")
    needed_by = models.DateField(null=True, blank=True)
    reason = models.TextField()

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SUBMITTED, db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="inventory_requisitions_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-requisition_id"]
        indexes = [
            models.Index(fields=["requested_by", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self) -> str:
        item_label = self.item.name if self.item_id else self.item_name
        return f"{item_label} - {self.quantity} {self.unit} ({self.get_status_display()})"

    @property
    def total_amount(self):
        if self.unit_price is None:
            return None
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))
