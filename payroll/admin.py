from django.contrib import admin
from .models import HourlyWorkEntry, SalaryBonus, SalaryDisbursement, SalaryPayment


@admin.register(SalaryPayment)
class SalaryPaymentAdmin(admin.ModelAdmin):
    list_display = ('period_month', 'employee', 'amount', 'status', 'recorded_by')
    list_filter = ('status', 'period_month', 'recorded_at')
    search_fields = ('employee__username', 'employee__first_name', 'employee__last_name')
    readonly_fields = ('salary_id', 'recorded_at')
    ordering = ('-period_month', 'employee__username')


@admin.register(SalaryBonus)
class SalaryBonusAdmin(admin.ModelAdmin):
    list_display = ("period_month", "employee", "bonus_name", "amount", "granted_by")
    list_filter = ("period_month", "granted_at")
    search_fields = ("employee__username", "employee__first_name", "employee__last_name", "bonus_name")
    readonly_fields = ("bonus_id", "granted_at")
    ordering = ("-period_month", "employee__username")


@admin.register(HourlyWorkEntry)
class HourlyWorkEntryAdmin(admin.ModelAdmin):
    list_display = ("work_date", "employee", "hours_worked", "hourly_rate", "gross_amount", "salary_payment")
    list_filter = ("work_date", "salary_payment")
    search_fields = ("employee__username", "employee__first_name", "employee__last_name", "notes")
    readonly_fields = ("work_entry_id", "recorded_at")
    ordering = ("-work_date", "employee__username")


@admin.register(SalaryDisbursement)
class SalaryDisbursementAdmin(admin.ModelAdmin):
    list_display = ("salary", "amount", "payment_method", "payment_reference", "paid_on", "paid_by")
    list_filter = ("payment_method", "paid_on")
    search_fields = ("salary__employee__username", "payment_reference", "accounting_entry_reference")
    readonly_fields = ("disbursement_id", "created_at")
    ordering = ("-paid_on", "-disbursement_id")
