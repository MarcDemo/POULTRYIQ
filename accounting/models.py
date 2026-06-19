from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType


class AccountingCode(models.Model):
    """
    Auto-generated accounting codes for all revenue and expense transactions.
    Examples: SE0034 (Sales Eggs), CRO2875 (Cost of Revenue), DEP2903 (Depreciation), ME384746 (Monthly Expenses)
    """
    
    ACCOUNT_TYPE_CHOICES = [
        ('REVENUE', 'Revenue'),
        ('COST_OF_REVENUE', 'Cost of Revenue'),
        ('DEPRECIATION', 'Depreciation'),
        ('MONTHLY_EXPENSES', 'Monthly Expenses'),
    ]
    
    PREFIX_CHOICES = [
        ('SE', 'Sales - Eggs'),
        ('SO', 'Sales - Off-Layer'),
        ('CRO', 'Cost of Revenue'),
        ('DEP', 'Depreciation'),
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
            prefix: Code prefix (SE, SO, CRO, DEP, ME)
            account_type: Type of account (REVENUE, COST_OF_REVENUE, DEPRECIATION, MONTHLY_EXPENSES)
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
