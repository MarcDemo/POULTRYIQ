from django.contrib import admin
from .models import ExpenseCategory, ExpenseTransaction, ExpenseAllocation


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'expense_type', 'account', 'is_active')
    list_filter = ('expense_type', 'is_active', 'account__account_type')
    search_fields = ('code', 'name', 'account__code', 'account__account_name')
    autocomplete_fields = ("account",)
    ordering = ('name',)


@admin.register(ExpenseTransaction)
class ExpenseTransactionAdmin(admin.ModelAdmin):
    list_display = ('expense_date', 'category', 'account', 'description', 'total_amount', 'status', 'created_by')
    list_filter = ('status', 'category', 'account__account_type', 'expense_date', 'period_year', 'period_month')
    search_fields = ('description', 'reference_no', 'expense_id', 'account__code', 'account__account_name')
    autocomplete_fields = ("account",)
    readonly_fields = ('expense_id', 'created_at', 'submitted_at', 'approved_at')
    ordering = ('-expense_date', '-expense_id')


@admin.register(ExpenseAllocation)
class ExpenseAllocationAdmin(admin.ModelAdmin):
    list_display = ('allocation_id', 'expense', 'batch', 'method', 'amount_allocated')
    list_filter = ('method', 'batch')
    search_fields = ('allocation_id', 'rationale')
    readonly_fields = ('allocation_id', 'created_at')
