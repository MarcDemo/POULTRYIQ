from django import forms
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm

from poultry.models import PoultryHouse

from .models import User, validate_house_assignment


class HouseAssignmentValidationMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["houses"].queryset = PoultryHouse.objects.order_by("house_code", "name")

    def clean(self):
        cleaned_data = super().clean()
        validate_house_assignment(
            cleaned_data.get("role"),
            cleaned_data.get("houses"),
        )
        return cleaned_data


class PoultryUserCreationForm(HouseAssignmentValidationMixin, AdminUserCreationForm):
    class Meta(AdminUserCreationForm.Meta):
        model = User
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
            "role",
            "phone_number",
            "houses",
        )


class PoultryUserChangeForm(HouseAssignmentValidationMixin, UserChangeForm):
    class Meta:
        model = User
        fields = "__all__"
