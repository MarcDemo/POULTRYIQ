from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .forms import PoultryUserChangeForm, PoultryUserCreationForm
from .models import Role, User


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
