from django.contrib import admin

from .models import Attendance, WagePayment, WelfareRequest, Worker


@admin.register(WelfareRequest)
class WelfareRequestAdmin(admin.ModelAdmin):
    list_display = ("worker", "request_type", "status", "created_at", "supervisor", "manager")
    list_filter = ("request_type", "status", "created_at")
    search_fields = ("worker__username", "worker__first_name", "worker__last_name", "title", "details")
    readonly_fields = ("created_at", "updated_at")


admin.site.register(Worker)
admin.site.register(Attendance)
admin.site.register(WagePayment)
