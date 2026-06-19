from django.contrib import admin
from .models import SalaryPayment


@admin.register(SalaryPayment)
class SalaryPaymentAdmin(admin.ModelAdmin):
    list_display = ('period_month', 'employee', 'amount', 'status', 'recorded_by')
    list_filter = ('status', 'period_month', 'recorded_at')
    search_fields = ('employee__username', 'employee__first_name', 'employee__last_name')
    readonly_fields = ('salary_id', 'recorded_at')
    ordering = ('-period_month', 'employee__username')
