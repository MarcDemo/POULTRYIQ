from django.contrib import admin
from .models import (
    AccountingCode,
    BalanceSheetAccount,
    FixedAssetAcquisition,
    AssetConstructionProject,
    AssetConstructionCostLine,
)


@admin.register(AccountingCode)
class AccountingCodeAdmin(admin.ModelAdmin):
    list_display = ('code', 'account_name', 'account_type', 'prefix', 'sequence_number', 'created_at')
    list_filter = ('account_type', 'prefix', 'created_at')
    search_fields = ('code', 'account_name')
    readonly_fields = ('code', 'sequence_number', 'created_at', 'updated_at')
    ordering = ('-created_at',)
    
    fieldsets = (
        ('Code Information', {
            'fields': ('code', 'prefix', 'account_type', 'sequence_number')
        }),
        ('Account Details', {
            'fields': ('account_name', 'content_type', 'object_id')
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


@admin.register(FixedAssetAcquisition)
class FixedAssetAcquisitionAdmin(admin.ModelAdmin):
    list_display = ("asset_name", "asset_category", "acquisition_date", "amount", "payment_method", "is_active")
    list_filter = ("asset_category", "is_active", "acquisition_date")
    search_fields = ("asset_name", "notes")
    ordering = ("-acquisition_date",)


@admin.register(AssetConstructionProject)
class AssetConstructionProjectAdmin(admin.ModelAdmin):
    list_display = ("project_name", "asset_category", "status", "start_date", "completed_date")
    list_filter = ("asset_category", "status")
    search_fields = ("project_name", "notes")
    ordering = ("-created_at",)


@admin.register(AssetConstructionCostLine)
class AssetConstructionCostLineAdmin(admin.ModelAdmin):
    list_display = ("project", "cost_date", "cost_item_name", "amount")
    list_filter = ("cost_date",)
    search_fields = ("project__project_name", "cost_item_name", "notes")
    ordering = ("-cost_date",)
