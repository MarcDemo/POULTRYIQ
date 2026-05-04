from django.contrib import admin
from .models import AlertType, Alert, AlertTemplate, AlertSchedule


@admin.register(AlertType)
class AlertTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'is_active']
    list_filter = ['is_active', 'code']
    search_fields = ['name', 'code']


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ['title', 'receiver', 'sender', 'priority', 'status', 'created_at']
    list_filter = ['status', 'priority', 'alert_type', 'created_at']
    search_fields = ['title', 'message', 'receiver__username', 'sender__username']
    readonly_fields = ['alert_id', 'created_at', 'updated_at', 'read_at']
    
    fieldsets = (
        ('Alert Info', {
            'fields': ('alert_id', 'alert_type', 'title', 'message', 'priority', 'status')
        }),
        ('Users', {
            'fields': ('sender', 'receiver')
        }),
        ('Context', {
            'fields': ('related_house', 'related_batch'),
            'classes': ('collapse',)
        }),
        ('Timing', {
            'fields': ('created_at', 'updated_at', 'read_at', 'due_date')
        }),
    )


@admin.register(AlertTemplate)
class AlertTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'alert_type', 'priority', 'is_active']
    list_filter = ['is_active', 'alert_type', 'priority']
    search_fields = ['name', 'code', 'title_template']


@admin.register(AlertSchedule)
class AlertScheduleAdmin(admin.ModelAdmin):
    list_display = ['template', 'receiver', 'related_house', 'next_alert_due', 'is_active']
    list_filter = ['is_active', 'template', 'next_alert_due']
    search_fields = ['receiver__username', 'template__name']
