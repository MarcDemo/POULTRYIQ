from django.contrib import admin
from .models import AccountingCode


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
