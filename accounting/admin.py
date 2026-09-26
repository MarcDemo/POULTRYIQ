from django.contrib import admin
from .models import (
    FinancialStatement,
    FinancialStatementAccountNature,
    AccountType,
    AccountingCode,
    ChartOfAccount,
    TransactionCategory,
    PaymentMethod,
    JournalEntry,
    JournalLine,
    BalanceSheetAccount,
    AssetCategory,
    FixedAssetAcquisition,
    AssetConstructionProject,
    AssetConstructionCostLine,
    Budget,
    BudgetLine,
    FiscalPeriod,
    DepreciationRun,
    FixedAssetDisposal,
    FixedAssetRevaluation,
)


class FinancialStatementAccountNatureInline(admin.TabularInline):
    model = FinancialStatementAccountNature
    extra = 1
    fields = ("account_nature", "display_order", "is_active")


@admin.register(FinancialStatement)
class FinancialStatementAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "display_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    ordering = ("display_order", "name")
    inlines = (FinancialStatementAccountNatureInline,)


class ChartOfAccountInline(admin.TabularInline):
    model = ChartOfAccount
    extra = 5
    fields = ("code", "system_code", "account_name", "allow_reconciliation", "show_on_suppliers", "description", "is_active")
    readonly_fields = ("code", "system_code")
    show_change_link = True


@admin.register(AccountType)
class AccountTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "prefix", "account_nature", "statement_memberships", "legacy_code", "show_on_suppliers", "is_active")
    list_filter = ("account_nature", "show_on_suppliers", "is_active")
    search_fields = ("name", "prefix", "legacy_code")
    fields = ("name", "account_nature", "legacy_code", "show_on_suppliers", "is_active", "prefix", "created_at", "updated_at")
    readonly_fields = ("prefix", "created_at", "updated_at")
    ordering = ("account_nature", "name")
    inlines = (ChartOfAccountInline,)

    @admin.display(description="Statements")
    def statement_memberships(self, obj):
        return obj.report_name


class TransactionCategoryInline(admin.TabularInline):
    model = TransactionCategory
    extra = 1
    fields = ("name", "description", "is_active")


class PaymentMethodInline(admin.TabularInline):
    model = PaymentMethod
    extra = 0
    fields = ("name", "is_active")


@admin.register(ChartOfAccount)
class ChartOfAccountAdmin(admin.ModelAdmin):
    list_display = ("code", "account_name", "account_type", "system_code", "account_report", "allow_reconciliation", "show_on_suppliers", "is_active")
    list_filter = (
        "account_type__account_nature",
        "account_type",
        "allow_reconciliation",
        "show_on_suppliers",
        "is_active",
    )
    search_fields = ("code", "system_code", "account_name", "description")
    autocomplete_fields = ("account_type",)
    readonly_fields = ("code", "created_at", "updated_at")
    list_editable = ("show_on_suppliers",)
    ordering = ("account_type__account_nature", "account_type__name", "code")
    inlines = (TransactionCategoryInline, PaymentMethodInline)

    @admin.display(description="Report")
    def account_report(self, obj):
        return obj.account_type.report_name


@admin.register(TransactionCategory)
class TransactionCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "account", "is_active")
    list_filter = ("is_active", "account__account_type__account_nature", "account__account_type")
    search_fields = ("name", "account__code", "account__account_name", "description")
    autocomplete_fields = ("account",)


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("name", "account", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "account__code", "account__account_name")
    autocomplete_fields = ("account",)


@admin.register(AccountingCode)
class AccountingCodeAdmin(admin.ModelAdmin):
    list_display = ('code', 'account_name', 'account_type', 'account', 'prefix', 'sequence_number', 'created_at')
    list_filter = ('account_type', 'prefix', 'created_at')
    search_fields = ('code', 'account_name', 'account__code', 'account__account_name')
    autocomplete_fields = ("account",)
    readonly_fields = ('code', 'sequence_number', 'created_at', 'updated_at')
    ordering = ('-created_at',)
    
    fieldsets = (
        ('Code Information', {
            'fields': ('code', 'prefix', 'account_type', 'sequence_number')
        }),
        ('Account Details', {
            'fields': ('account', 'account_name', 'content_type', 'object_id')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(BalanceSheetAccount)
class BalanceSheetAccountAdmin(admin.ModelAdmin):
    list_display = ("code", "account_name", "group", "account_type", "allow_reconciliation", "is_active")
    list_filter = ("group", "account_type", "allow_reconciliation", "is_active")
    search_fields = ("code", "account_name", "description")
    ordering = ("group", "account_type", "code")


@admin.register(AssetCategory)
class AssetCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "asset_account", "is_land", "is_depreciable", "useful_life_years", "is_active")
    list_filter = ("is_land", "is_depreciable", "is_construction_only", "is_active")
    autocomplete_fields = (
        "asset_account",
        "accumulated_depreciation_account",
        "depreciation_expense_account",
    )
    search_fields = (
        "name",
        "legacy_code",
        "asset_account__code",
        "asset_account__account_name",
    )


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 0
    readonly_fields = ("account", "debit", "credit", "memo")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ("fdn", "reference", "entry_date", "description", "status", "total_debits", "total_credits")
    list_filter = ("status", "entry_date", "lines__account__account_type__account_nature")
    search_fields = ("fdn", "reference", "description", "lines__account__code", "lines__account__account_name")
    readonly_fields = ("fdn", "reference", "entry_date", "description", "status", "content_type", "object_id", "created_by", "created_at", "void_reason", "voided_by", "voided_at", "reversal_of")
    inlines = (JournalLineInline,)
    ordering = ("-entry_date", "-id")

    def has_add_permission(self, request):
        return False


@admin.register(FixedAssetAcquisition)
class FixedAssetAcquisitionAdmin(admin.ModelAdmin):
    list_display = ("asset_name", "asset_category", "acquisition_date", "amount", "payment_method_display", "supplier", "is_active")
    list_filter = ("asset_category", "is_active", "acquisition_date")
    search_fields = ("asset_name", "notes")
    autocomplete_fields = ("asset_category", "payment_method_option", "supplier")
    ordering = ("-acquisition_date",)


@admin.register(AssetConstructionProject)
class AssetConstructionProjectAdmin(admin.ModelAdmin):
    list_display = ("project_name", "asset_category", "status", "start_date", "completed_date")
    list_filter = ("asset_category", "status")
    search_fields = ("project_name", "notes")
    autocomplete_fields = ("asset_category",)
    ordering = ("-created_at",)


@admin.register(AssetConstructionCostLine)
class AssetConstructionCostLineAdmin(admin.ModelAdmin):
    list_display = ("project", "cost_date", "cost_item_name", "amount")
    list_filter = ("cost_date",)
    search_fields = ("project__project_name", "cost_item_name", "notes")
    ordering = ("-cost_date",)


class BudgetLineInline(admin.TabularInline):
    model = BudgetLine
    extra = 0
    autocomplete_fields = ("account", "fiscal_period")


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ("name", "fiscal_year", "version", "status", "approved_by", "approved_at")
    list_filter = ("fiscal_year", "status")
    search_fields = ("name", "notes")
    readonly_fields = ("created_at", "updated_at", "approved_at")
    inlines = (BudgetLineInline,)


@admin.register(FiscalPeriod)
class FiscalPeriodAdmin(admin.ModelAdmin):
    list_display = ("fiscal_year", "period_number", "label", "start_date", "end_date", "is_closed")
    list_filter = ("fiscal_year", "is_closed")
    search_fields = ("label",)
    ordering = ("-fiscal_year", "period_number")


@admin.register(DepreciationRun)
class DepreciationRunAdmin(admin.ModelAdmin):
    list_display = ("period_year", "period_month", "status", "posted_count", "skipped_count", "total_amount", "run_by", "ran_at")
    list_filter = ("status", "period_year")
    readonly_fields = ("created_at", "ran_at")


@admin.register(FixedAssetDisposal)
class FixedAssetDisposalAdmin(admin.ModelAdmin):
    list_display = ("asset", "disposal_date", "proceeds", "book_value", "gain_loss", "journal_entry")
    list_filter = ("disposal_date",)
    search_fields = ("asset__asset_name", "reason", "journal_entry__fdn")
    autocomplete_fields = ("asset", "payment_method_option")
    readonly_fields = ("accumulated_depreciation", "book_value", "gain_loss", "depreciation_entry", "journal_entry", "created_at")


@admin.register(FixedAssetRevaluation)
class FixedAssetRevaluationAdmin(admin.ModelAdmin):
    list_display = ("asset", "revaluation_date", "old_book_value", "new_value", "adjustment", "journal_entry")
    list_filter = ("revaluation_date",)
    search_fields = ("asset__asset_name", "reason", "journal_entry__fdn")
    autocomplete_fields = ("asset",)
    readonly_fields = ("old_book_value", "adjustment", "journal_entry", "created_at")
