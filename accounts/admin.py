from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .forms import PoultryUserChangeForm, PoultryUserCreationForm
from .models import EndOfDayNote, InvestorCapitalTransaction, Role, User


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active")
    list_filter = ("is_active", "code")
    search_fields = ("name", "code")
    ordering = ("name",)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = PoultryUserCreationForm
    form = PoultryUserChangeForm

    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "role",
        "assigned_houses",
        "is_staff",
        "is_active",
        "is_locked",
    )
    list_filter = ("role", "is_staff", "is_superuser", "is_active", "is_locked")
    search_fields = ("username", "first_name", "last_name", "email", "phone_number")
    ordering = ("username",)
    filter_horizontal = (*BaseUserAdmin.filter_horizontal, "houses")

    fieldsets = BaseUserAdmin.fieldsets + (
        ("Farm Access", {"fields": ("role", "phone_number", "houses", "is_locked")}),
    )

    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        (
            "Farm Access",
            {
                "classes": ("wide",),
                "fields": (
                    "first_name",
                    "last_name",
                    "email",
                    "role",
                    "phone_number",
                    "houses",
                ),
            },
        ),
    )

    @admin.display(description="Poultry Houses")
    def assigned_houses(self, obj):
        houses = obj.houses.order_by("house_code").values_list("house_code", flat=True)
        return ", ".join(houses) or "-"


@admin.register(InvestorCapitalTransaction)
class InvestorCapitalTransactionAdmin(admin.ModelAdmin):
    list_display = ("transaction_date", "transaction_type", "amount", "recorded_by", "created_at")
    list_filter = ("transaction_type", "transaction_date")
    search_fields = ("notes", "recorded_by__username", "recorded_by__first_name", "recorded_by__last_name")
    ordering = ("-transaction_date", "-created_at")


@admin.register(EndOfDayNote)
class EndOfDayNoteAdmin(admin.ModelAdmin):
    list_display = ("summary_date", "updated_by", "updated_at")
    search_fields = ("notes", "updated_by__username", "updated_by__first_name", "updated_by__last_name")
    date_hierarchy = "summary_date"
    ordering = ("-summary_date",)
    readonly_fields = ("created_at", "updated_at")
