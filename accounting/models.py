from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.db.models import Sum
from django.core.exceptions import ValidationError
from datetime import timedelta


class AccountingCode(models.Model):
    """
    Auto-generated accounting codes for all revenue and expense transactions.
    Examples: SE0034 (Sales Eggs), CRO2875 (Cost of Revenue), DEP2903 (Depreciation), EXP0001 (Expenses)
    """
    
    ACCOUNT_TYPE_CHOICES = [
        ('REVENUE', 'Revenue'),
        ('OTHER_INCOME', 'Other Income'),
        ('COST_OF_REVENUE', 'Cost of Revenue'),
        ('DEPRECIATION', 'Depreciation'),
        ('EXPENSES', 'Expenses'),
        ('OTHER_EXPENSES', 'Other Expenses'),
        ('MONTHLY_EXPENSES', 'Monthly Expenses'),
    ]
    
    PREFIX_CHOICES = [
        ('SE', 'Sales - Eggs'),
        ('SO', 'Sales - Off-Layer'),
        ('OIN', 'Other Income'),
        ('CRO', 'Cost of Revenue'),
        ('DEP', 'Depreciation'),
        ('EXP', 'Expenses'),
        ('OEX', 'Other Expenses'),
        ('ME', 'Monthly Expenses'),
    ]
    
    # Core fields
    code = models.CharField(
        max_length=20, 
        unique=True,
        db_index=True,
        help_text="Auto-generated code (e.g., SE0034, CRO2875)"
    )
    prefix = models.CharField(max_length=3, choices=PREFIX_CHOICES)
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPE_CHOICES, db_index=True)
    account_name = models.CharField(
        max_length=150,
        help_text="Product/category name (e.g., 'Eggs', 'Feed', 'Utilities')"
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


class AssetCategory(models.TextChoices):
    # Land
    LAND = "LAND", "Land"
    # Buildings and structures
    POULTRY_HOUSE = "POULTRY_HOUSE", "Poultry House"
    BROILER_HOUSE = "BROILER_HOUSE", "Broiler House"
    LAYER_HOUSE = "LAYER_HOUSE", "Layer House"
    HATCHERY_BUILDING = "HATCHERY_BUILDING", "Hatchery Building"
    FEED_STORE = "FEED_STORE", "Feed Store / Warehouse"
    OFFICE_BUILDING = "OFFICE_BUILDING", "Office Building"
    STAFF_QUARTERS = "STAFF_QUARTERS", "Staff Quarters"
    SECURITY_BOOTH = "SECURITY_BOOTH", "Security Booth"
    GENERAL_BUILDING = "GENERAL_BUILDING", "General Building"
    # Poultry-specific equipment
    CAGES_REARING = "CAGES_REARING", "Cages & Rearing Equipment"
    FEEDERS = "FEEDERS", "Feeders"
    DRINKERS_WATERING = "DRINKERS_WATERING", "Drinkers / Watering Systems"
    INCUBATORS = "INCUBATORS", "Incubators & Hatchery Equipment"
    EGG_HANDLING = "EGG_HANDLING", "Egg Handling Equipment"
    EGG_GRADING = "EGG_GRADING", "Egg Grading & Packing Equipment"
    VENTILATION = "VENTILATION", "Ventilation & Climate Control Systems"
    LIGHTING = "LIGHTING", "Lighting Systems"
    BIOSECURITY = "BIOSECURITY", "Biosecurity Equipment"
    WASTE_MANAGEMENT = "WASTE_MANAGEMENT", "Waste Management Systems"
    # Farm machinery and utilities
    FEED_MIXING = "FEED_MIXING", "Feed Mixing Equipment"
    FEED_STORAGE = "FEED_STORAGE", "Feed Storage Silos / Bins"
    TRACTOR_MACHINERY = "TRACTOR_MACHINERY", "Tractor & Farm Machinery"
    GENERATOR = "GENERATOR", "Generator / Electrical Installation"
    BOREHOLE_WATER = "BOREHOLE_WATER", "Borehole & Water Supply Systems"
    COLD_STORAGE = "COLD_STORAGE", "Cold Storage / Refrigeration"
    # Vehicles
    VEHICLE = "VEHICLE", "Vehicle (Delivery / Farm)"
    # IT and office
    COMPUTER_EQUIPMENT = "COMPUTER_EQUIPMENT", "Computer Equipment"
    OFFICE_EQUIPMENT = "OFFICE_EQUIPMENT", "Office Equipment"
    FURNITURE_FITTINGS = "FURNITURE_FITTINGS", "Furniture & Fittings"
    SECURITY_SYSTEM = "SECURITY_SYSTEM", "Security System (CCTV / Alarm)"
    SOFTWARE = "SOFTWARE", "Software"
    # Other
    HARDWARE_EQUIPMENT = "HARDWARE_EQUIPMENT", "Hardware Equipment"
    OTHER = "OTHER", "Other"


class FixedAssetAcquisition(models.Model):
    class DepreciationMethod(models.TextChoices):
        STRAIGHT_LINE = "STRAIGHT_LINE", "Straight Line"

    asset_name = models.CharField(max_length=150)
    asset_category = models.CharField(max_length=40, choices=AssetCategory.choices, db_index=True)
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
        return f"{self.get_asset_category_display()} / {self.asset_name}"

    def clean(self):
        if self.residual_value and self.residual_value > self.amount:
            raise ValidationError("Residual value cannot exceed asset amount.")

        if self.asset_category == AssetCategory.LAND:
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
        if self.asset_category == AssetCategory.LAND:
            self.is_depreciable = False
            self.useful_life_years = None
            self.residual_value = 0
        if not self.in_service_date:
            self.in_service_date = self.acquisition_date
        self.full_clean()
        super().save(*args, **kwargs)


class AssetConstructionProject(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED = "COMPLETED", "Completed"

    project_name = models.CharField(max_length=150)
    asset_category = models.CharField(max_length=40, choices=AssetCategory.choices, db_index=True)
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

    @property
    def total_cost(self):
        return self.cost_lines.aggregate(total=Sum("amount"))["total"] or 0

    def save(self, *args, **kwargs):
        if self.asset_category == AssetCategory.LAND:
            self.is_depreciable = False
            self.useful_life_years = None
            self.residual_value = 0
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
