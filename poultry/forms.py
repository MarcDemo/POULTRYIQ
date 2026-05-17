from django import forms
from inventory.models import Supplier
from .models import PoultryBatch

class PoultryBatchForm(forms.ModelForm):
    supplier_name = forms.ChoiceField(
        choices=[],
        required=False,
        label="Supplier Name",
    )

    class Meta:
        model = PoultryBatch
        fields = [
            "house",
            "breed",
            "supplier_name",
            "date_stocked",
            "initial_quantity",
            "initial_age_days",
            "notes",
            "amount_paid",
        ]

        widgets = {
            "date_stocked": forms.DateInput(attrs={"type": "date"}),
            "initial_age_days": forms.NumberInput(attrs={"type": "number"}),
            "amount_paid": forms.NumberInput(attrs={"type": "number", "step": "0.01"}),
            "expected_lay_start": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        supplier_choices = [("", "-- Select supplier --")]
        supplier_choices.extend(
            (supplier.name, supplier.name)
            for supplier in Supplier.objects.filter(is_active=True).order_by("name")
        )
        self.fields["supplier_name"].choices = supplier_choices
        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-control"})
