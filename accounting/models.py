from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.db.models import Sum
from django.core.exceptions import ValidationError
from datetime import timedelta


ACCOUNT_NATURE_CHOICES = [
    ("ASSET", "Asset"),
    ("LIABILITY", "Liability"),
    ("EQUITY", "Equity"),
    ("INCOME", "Income"),
    ("EXPENSE", "Expense"),
]


class FinancialStatement(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=30, unique=True, db_index=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name


class AccountType(models.Model):
    class AccountNature(models.TextChoices):
        ASSET = "ASSET", "Asset"
        LIABILITY = "LIABILITY", "Liability"
        EQUITY = "EQUITY", "Equity"
        INCOME = "INCOME", "Income"
        EXPENSE = "EXPENSE", "Expense"

    name = models.CharField(max_length=100, unique=True)
    prefix = models.CharField(
        max_length=10,
        unique=True,
        editable=False,
        help_text="Generated from the account type name and kept stable after creation.",
    )
    account_nature = models.CharField(
        max_length=20,
        choices=AccountNature.choices,
        db_index=True,
        help_text="The accounting nature used for totals and normal balance logic.",
    )
    legacy_code = models.CharField(
        max_length=40,
        blank=True,
        unique=True,
        null=True,
        help_text="Optional old reporting code, used only for backwards-compatible reports.",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    show_on_suppliers = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Allow chart accounts under this type to be selected on the suppliers page.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["account_nature", "name"]

    def __str__(self):
        return f"{self.name} ({self.prefix})"

    @staticmethod
    def _prefix_seed(name):
        letters = "".join(char for char in name.upper() if char.isalnum())
        return (letters[:2] or "AC").ljust(2, "X")

    @classmethod
    def generate_prefix(cls, name):
        letters = "".join(char for char in name.upper() if char.isalnum())
        seed = (letters[:2] or "AC").ljust(2, "X")
        prefix = seed
        length = 3
        while cls.objects.filter(prefix=prefix).exists():
            if length <= len(letters):
                prefix = letters[:length]
                length += 1
            else:
                suffix = 2
                while cls.objects.filter(prefix=f"{seed}{suffix}").exists():
                    suffix += 1
                prefix = f"{seed}{suffix}"
                break
        return prefix

    def save(self, *args, **kwargs):
        if not self.prefix:
            self.prefix = self.generate_prefix(self.name)
        super().save(*args, **kwargs)

    @property
    def report_code(self):
        statements = FinancialStatement.objects.filter(
            account_natures__account_nature=self.account_nature,
            account_natures__is_active=True,
            is_active=True,
        )
        return ", ".join(statements.values_list("code", flat=True))

    @property
    def report_name(self):
        statements = FinancialStatement.objects.filter(
            account_natures__account_nature=self.account_nature,
            account_natures__is_active=True,
            is_active=True,
        )
        names = list(statements.values_list("name", flat=True))
        return ", ".join(names) if names else self.get_account_nature_display()

    @property
    def report_section_code(self):
        return self.account_nature

    @property
    def report_section_name(self):
        return self.get_account_nature_display()

    @property
    def is_balance_sheet_type(self):
        return FinancialStatementAccountNature.objects.filter(
            statement__code="BALANCE_SHEET",
            statement__is_active=True,
            account_nature=self.account_nature,
            is_active=True,
        ).exists() or self.account_nature in {
            self.AccountNature.ASSET,
            self.AccountNature.LIABILITY,
            self.AccountNature.EQUITY,
        }

    @property
    def is_profit_loss_type(self):
        return FinancialStatementAccountNature.objects.filter(
            statement__code="PROFIT_LOSS",
            statement__is_active=True,
            account_nature=self.account_nature,
            is_active=True,
        ).exists() or self.account_nature in {
            self.AccountNature.INCOME,
            self.AccountNature.EXPENSE,
        }


class FinancialStatementAccountNature(models.Model):
    statement = models.ForeignKey(FinancialStatement, on_delete=models.CASCADE, related_name="account_natures")
    account_nature = models.CharField(max_length=20, choices=ACCOUNT_NATURE_CHOICES, db_index=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["statement__display_order", "display_order", "account_nature"]
        unique_together = ("statement", "account_nature")

    def __str__(self):
        return f"{self.statement} / {self.get_account_nature_display()}"


class ChartOfAccount(models.Model):
    code = models.CharField(max_length=20, unique=True, db_index=True, blank=True)
    system_code = models.CharField(
        max_length=80,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text="Stable internal code for built-in accounts. Admins may rename the account without breaking posting.",
    )
    account_name = models.CharField(max_length=150)
    account_type = models.ForeignKey(AccountType, on_delete=models.PROTECT, related_name="accounts")
    allow_reconciliation = models.BooleanField(default=False)
    show_on_suppliers = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Allow this chart account to be selected for supplier products.",
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["account_type__account_nature", "account_type__name", "code"]
        indexes = [
            models.Index(fields=["account_type", "code"]),
        ]

    def __str__(self):
        return f"{self.code} - {self.account_name}"

    @classmethod
    def get_next_code(cls, account_type):
        prefix = account_type.prefix if isinstance(account_type, AccountType) else str(account_type)
        last_account = cls.objects.filter(code__startswith=prefix).order_by("-code").first()
        next_sequence = 1
        if last_account:
            suffix = last_account.code.replace(prefix, "", 1)
            if suffix.isdigit():
                next_sequence = int(suffix) + 1
        return f"{prefix}{next_sequence:04d}"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self.get_next_code(self.account_type)
        super().save(*args, **kwargs)

    @property
    def group(self):
        return {
            AccountType.AccountNature.ASSET: "ASSETS",
            AccountType.AccountNature.LIABILITY: "LIABILITIES",
            AccountType.AccountNature.EQUITY: "EQUITY",
        }.get(self.account_type.account_nature, self.account_type.account_nature)

    def get_account_type_display(self):
        return self.account_type.name


class TransactionCategory(models.Model):
    name = models.CharField(max_length=120, unique=True)
    account = models.ForeignKey(ChartOfAccount, on_delete=models.PROTECT, related_name="transaction_categories")
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Transaction categories"

    def __str__(self):
        return f"{self.name} -> {self.account}"


class PaymentMethod(models.Model):
    name = models.CharField(max_length=100, unique=True)
    account = models.ForeignKey(ChartOfAccount, on_delete=models.PROTECT, related_name="payment_methods")
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} -> {self.account}"


class JournalEntry(models.Model):
    class Status(models.TextChoices):
        POSTED = "POSTED", "Posted"
        VOID = "VOID", "Void"

    entry_date = models.DateField(db_index=True)
    reference = models.CharField(max_length=80, unique=True, db_index=True)
    description = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.POSTED, db_index=True)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, null=True, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    content_object = GenericForeignKey("content_type", "object_id")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="journal_entries_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-entry_date", "-id"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
            models.Index(fields=["status", "entry_date"]),
        ]
        verbose_name_plural = "Journal entries"

    def __str__(self):
        return f"{self.reference} - {self.description}"

    @property
    def total_debits(self):
        return self.lines.aggregate(total=Sum("debit"))["total"] or 0

    @property
    def total_credits(self):
        return self.lines.aggregate(total=Sum("credit"))["total"] or 0


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(ChartOfAccount, on_delete=models.PROTECT, related_name="journal_lines")
    debit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    memo = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["entry_id", "id"]
        indexes = [
            models.Index(fields=["account"]),
        ]

    def clean(self):
        if self.debit and self.credit:
            raise ValidationError("A journal line cannot have both debit and credit.")
        if not self.debit and not self.credit:
            raise ValidationError("A journal line must have either debit or credit.")

    def __str__(self):
        amount = self.debit or self.credit
        side = "Dr" if self.debit else "Cr"
        return f"{self.entry.reference} {side} {self.account}: {amount}"


class AccountingCode(models.Model):
    """
    Auto-generated accounting codes for all revenue and expense transactions.
    Examples: SE0034 (Sales Eggs), CRO2875 (Cost of Revenue), DEP2903 (Depreciation), EXP0001 (Expenses)
    """
    
    # Core fields
    code = models.CharField(
        max_length=20, 
        unique=True,
        db_index=True,
        help_text="Auto-generated code (e.g., SE0034, CRO2875)"
    )
    prefix = models.CharField(max_length=10)
    account_type = models.CharField(max_length=40, db_index=True)
    account_name = models.CharField(
        max_length=150,
        help_text="Product/category name (e.g., 'Eggs', 'Feed', 'Utilities')"
    )
    account = models.ForeignKey(
        ChartOfAccount,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transaction_references",
    )
    
    # Sequence tracking per prefix
    sequence_number = models.IntegerField(
        help_text="Sequential number for this prefix (used to generate code)"
    )
    
    # Generic relationship to source transaction
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="Content type of the related transaction (SaleInvoice, ExpenseTransaction, etc.)"
    )
    object_id = models.PositiveIntegerField(null=True, blank=True)
    content_object = GenericForeignKey('content_type', 'object_id')
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['prefix', 'account_type']),
            models.Index(fields=['account_type', 'created_at']),
        ]
        verbose_name = 'Accounting Code'
        verbose_name_plural = 'Accounting Codes'
    
    def __str__(self):
        return f"{self.code} - {self.account_name}"

    def get_account_type_display(self):
        return self.account.account_type.name if self.account_id else self.account_type.replace("_", " ").title()
    
    @classmethod
    def get_next_code(cls, prefix, account_type):
        """
        Generate the next accounting code for a given prefix.
        Format: PREFIX + 4-digit sequence number (e.g., SE0001, SE0002, etc.)
        """
        # Get the last sequence number for this prefix
        last_record = cls.objects.filter(prefix=prefix).order_by('-sequence_number').first()
        next_sequence = (last_record.sequence_number + 1) if last_record else 1
        
        # Generate code with 4-digit zero-padded sequence
        code = f"{prefix}{next_sequence:04d}"
        return code, next_sequence
    
    @classmethod
    def create_or_get_accounting_code(cls, prefix, account_type, account_name, content_object=None):
        """
        Create or retrieve an accounting code for an account.
        If account already has a code, return the existing one.
        Otherwise, generate a new code.
        
        Args:
            prefix: Code prefix (SE, SO, OIN, CRO, DEP, EXP, OEX, ME)
            account_type: Type of account (REVENUE, OTHER_INCOME, COST_OF_REVENUE, DEPRECIATION, EXPENSES, OTHER_EXPENSES, MONTHLY_EXPENSES)
            account_name: Name of the account/product (e.g., 'Eggs', 'Feed')
            content_object: Optional Django model instance to link to this code
            
        Returns:
            AccountingCode instance
        """
        # Check if code already exists for this account
        if content_object:
            content_type = ContentType.objects.get_for_model(content_object)
            existing = cls.objects.filter(
                content_type=content_type,
                object_id=content_object.pk
            ).first()
            if existing:
                return existing
        
        # Reuse account-name codes only when no source transaction is linked.
        # Transaction-backed codes must stay one-to-one with their source object
        # so reports can read the correct amount for every sale or expense.
        if not content_object:
            existing = cls.objects.filter(
                prefix=prefix,
                account_type=account_type,
                account_name=account_name
            ).first()
            if existing:
                return existing
        
        # Generate new code
        code, sequence_number = cls.get_next_code(prefix, account_type)
        
        # Create new accounting code
        acc_code = cls.objects.create(
            code=code,
            prefix=prefix,
            account_type=account_type,
            account_name=account_name,
            sequence_number=sequence_number,
            content_type=ContentType.objects.get_for_model(content_object) if content_object else None,
            object_id=content_object.pk if content_object else None,
        )
        
        return acc_code

    @classmethod
    def create_for(cls, account, content_object=None, description=""):
        if content_object:
            content_type = ContentType.objects.get_for_model(content_object)
            existing = cls.objects.filter(content_type=content_type, object_id=content_object.pk).first()
            if existing:
                return existing

        prefix = account.account_type.prefix
        last_record = cls.objects.filter(prefix=prefix).order_by("-sequence_number").first()
        next_sequence = (last_record.sequence_number + 1) if last_record else 1
        return cls.objects.create(
            code=f"{prefix}-T{next_sequence:04d}",
            prefix=prefix,
            account_type=account.account_type.legacy_code or account.account_type.name.upper().replace(" ", "_"),
            account_name=account.account_name,
            account=account,
            sequence_number=next_sequence,
            content_type=ContentType.objects.get_for_model(content_object) if content_object else None,
            object_id=content_object.pk if content_object else None,
        )


class BalanceSheetAccount(models.Model):
    ACCOUNT_TYPE_PREFIXES = {
        "FIXED_ASSET": "FA",
        "ACCUMULATED_DEPRECIATION": "AD",
        "CURRENT_ASSET": "CA",
        "RECEIVABLE": "REC",
        "PREPAYMENT": "PRE",
        "BANK_AND_CASH": "BC",
        "NON_CURRENT_LIABILITY": "NCL",
        "CURRENT_LIABILITY": "CL",
        "PAYABLE": "PAY",
        "EQUITY": "EQ",
        "CURRENT_YEAR_EARNINGS": "CYE",
    }

    class Group(models.TextChoices):
        ASSETS = "ASSETS", "Assets"
        LIABILITIES = "LIABILITIES", "Liabilities"
        EQUITY = "EQUITY", "Equity"

    class AccountType(models.TextChoices):
        FIXED_ASSET = "FIXED_ASSET", "Fixed Asset"
        ACCUMULATED_DEPRECIATION = "ACCUMULATED_DEPRECIATION", "Accumulated Depreciation"
        CURRENT_ASSET = "CURRENT_ASSET", "Current Asset"
        RECEIVABLE = "RECEIVABLE", "Receivable"
        PREPAYMENT = "PREPAYMENT", "Prepayment"
        BANK_AND_CASH = "BANK_AND_CASH", "Bank and Cash"
        NON_CURRENT_LIABILITY = "NON_CURRENT_LIABILITY", "Non Current Liability"
        CURRENT_LIABILITY = "CURRENT_LIABILITY", "Current Liability"
        PAYABLE = "PAYABLE", "Payable"
        EQUITY = "EQUITY", "Equity"
        CURRENT_YEAR_EARNINGS = "CURRENT_YEAR_EARNINGS", "Current Year Earnings"

    code = models.CharField(max_length=20, unique=True, db_index=True)
    account_name = models.CharField(max_length=150)
    group = models.CharField(max_length=20, choices=Group.choices, db_index=True)
    account_type = models.CharField(max_length=30, choices=AccountType.choices, db_index=True)
    allow_reconciliation = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["group", "account_type", "code"]
        indexes = [
            models.Index(fields=["group", "account_type"]),
        ]

    def __str__(self):
        return f"{self.code} - {self.account_name}"

    @classmethod
    def get_prefix_for_type(cls, account_type):
        return cls.ACCOUNT_TYPE_PREFIXES.get(account_type, "BS")

    @classmethod
    def get_next_code(cls, account_type):
        prefix = cls.get_prefix_for_type(account_type)
        last_account = cls.objects.filter(code__startswith=prefix).order_by("-code").first()
        next_sequence = 1
        if last_account:
            suffix = last_account.code.replace(prefix, "", 1)
            if suffix.isdigit():
                next_sequence = int(suffix) + 1
        return f"{prefix}{next_sequence:04d}"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self.get_next_code(self.account_type)
        super().save(*args, **kwargs)


class AssetCategory(models.Model):
    name = models.CharField(max_length=120, unique=True)
    asset_account = models.ForeignKey(
        ChartOfAccount,
        on_delete=models.PROTECT,
        related_name="asset_categories",
    )
    accumulated_depreciation_account = models.ForeignKey(
        ChartOfAccount,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="accumulated_depreciation_categories",
    )
    depreciation_expense_account = models.ForeignKey(
        ChartOfAccount,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="depreciation_expense_categories",
    )
    useful_life_years = models.PositiveIntegerField(null=True, blank=True)
    is_land = models.BooleanField(default=False, db_index=True)
    is_depreciable = models.BooleanField(default=True, db_index=True)
    is_construction_only = models.BooleanField(default=False, db_index=True)
    legacy_code = models.CharField(max_length=40, blank=True, unique=True, null=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Asset categories"

    def __str__(self):
        return self.name


class FixedAssetAcquisition(models.Model):
    class DepreciationMethod(models.TextChoices):
        STRAIGHT_LINE = "STRAIGHT_LINE", "Straight Line"

    asset_name = models.CharField(max_length=150)
    asset_category = models.ForeignKey(AssetCategory, on_delete=models.PROTECT, related_name="acquisitions")
    source_project = models.OneToOneField(
        "AssetConstructionProject",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="capitalized_asset",
    )
    acquisition_date = models.DateField(db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    is_depreciable = models.BooleanField(default=True)
    depreciation_method = models.CharField(
        max_length=30,
        choices=DepreciationMethod.choices,
        default=DepreciationMethod.STRAIGHT_LINE,
    )
    useful_life_years = models.PositiveIntegerField(null=True, blank=True)
    residual_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    in_service_date = models.DateField(null=True, blank=True, db_index=True)
    payment_method = models.CharField(max_length=30, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="fixed_asset_acquisitions_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-acquisition_date", "-id"]

    def __str__(self):
        return f"{self.asset_category} / {self.asset_name}"

    def get_asset_category_display(self):
        return str(self.asset_category)

    def clean(self):
        if self.residual_value and self.residual_value > self.amount:
            raise ValidationError("Residual value cannot exceed asset amount.")

        if self.asset_category_id and self.asset_category.is_land:
            self.is_depreciable = False

        if self.is_depreciable and (not self.useful_life_years or self.useful_life_years <= 0):
            raise ValidationError("Useful life years is required for depreciable assets.")

    @property
    def depreciable_base(self):
        base = self.amount - (self.residual_value or 0)
        return base if base > 0 else 0

    def _life_end_date(self):
        if not self.in_service_date or not self.useful_life_years:
            return None
        try:
            return self.in_service_date.replace(year=self.in_service_date.year + self.useful_life_years)
        except ValueError:
            # Handle leap-year edge case by rolling to Feb 28.
            return self.in_service_date.replace(month=2, day=28, year=self.in_service_date.year + self.useful_life_years)

    def accumulated_depreciation(self, as_of_date):
        if not self.is_depreciable or not self.in_service_date or not self.useful_life_years:
            return 0
        if as_of_date < self.in_service_date:
            return 0

        life_end = self._life_end_date()
        if not life_end:
            return 0

        total_days = (life_end - self.in_service_date).days
        if total_days <= 0:
            return 0

        cutoff = min(as_of_date, life_end)
        elapsed_days = (cutoff - self.in_service_date).days
        if elapsed_days <= 0:
            return 0

        depreciation = (self.depreciable_base * elapsed_days) / total_days
        return depreciation if depreciation < self.depreciable_base else self.depreciable_base

    def period_depreciation(self, start_date, end_date):
        if end_date < start_date:
            return 0
        previous_day = start_date - timedelta(days=1)
        return self.accumulated_depreciation(end_date) - self.accumulated_depreciation(previous_day)

    def save(self, *args, **kwargs):
        if self.asset_category_id and self.asset_category.is_land:
            self.is_depreciable = False
            self.useful_life_years = None
            self.residual_value = 0
        elif self.asset_category_id and self.useful_life_years is None:
            self.useful_life_years = self.asset_category.useful_life_years
        if not self.in_service_date:
            self.in_service_date = self.acquisition_date
        self.full_clean()
        super().save(*args, **kwargs)


class AssetConstructionProject(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED = "COMPLETED", "Completed"

    project_name = models.CharField(max_length=150)
    asset_category = models.ForeignKey(AssetCategory, on_delete=models.PROTECT, related_name="construction_projects")
    start_date = models.DateField(db_index=True)
    completed_date = models.DateField(null=True, blank=True, db_index=True)
    in_service_date = models.DateField(null=True, blank=True, db_index=True)
    is_depreciable = models.BooleanField(default=True)
    useful_life_years = models.PositiveIntegerField(null=True, blank=True)
    residual_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    capitalized_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    capitalized_on = models.DateField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS, db_index=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="asset_construction_projects_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.project_name} ({self.get_status_display()})"

    def get_asset_category_display(self):
        return str(self.asset_category)

    @property
    def total_cost(self):
        return self.cost_lines.aggregate(total=Sum("amount"))["total"] or 0

    def clean(self):
        if self.residual_value and self.residual_value < 0:
            raise ValidationError("Residual value cannot be negative.")
        if self.asset_category_id and self.asset_category.is_land:
            self.is_depreciable = False
        if self.is_depreciable and (not self.useful_life_years or self.useful_life_years <= 0):
            raise ValidationError("Useful life years is required for depreciable construction projects.")
        if self.completed_date and self.start_date and self.completed_date < self.start_date:
            raise ValidationError("Completion date cannot be before the project start date.")
        if self.in_service_date and self.start_date and self.in_service_date < self.start_date:
            raise ValidationError("In-service date cannot be before the project start date.")

    def save(self, *args, **kwargs):
        if self.asset_category_id and self.asset_category.is_land:
            self.is_depreciable = False
            self.useful_life_years = None
            self.residual_value = 0
        elif self.asset_category_id and self.useful_life_years is None:
            self.useful_life_years = self.asset_category.useful_life_years
        self.full_clean()
        super().save(*args, **kwargs)


class AssetConstructionCostLine(models.Model):
    project = models.ForeignKey(
        AssetConstructionProject,
        on_delete=models.CASCADE,
        related_name="cost_lines",
    )
    cost_date = models.DateField(db_index=True)
    cost_item_name = models.CharField(max_length=150)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="asset_construction_cost_lines_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-cost_date", "-id"]

    def __str__(self):
        return f"{self.project.project_name} - {self.cost_item_name}: {self.amount}"

    def clean(self):
        if self.amount and self.amount <= 0:
            raise ValidationError("Construction cost amount must be greater than zero.")
        if self.project_id and self.project.status != AssetConstructionProject.Status.IN_PROGRESS:
            raise ValidationError("Cannot add construction costs to a completed project.")
        if self.project_id and self.cost_date and self.cost_date < self.project.start_date:
            raise ValidationError("Construction cost date cannot be before the project start date.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
