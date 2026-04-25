from django import forms
from .models import PoultryBatch

class PoultryBatchForm(forms.ModelForm):
    class Meta:
        model = PoultryBatch
        fields = [
            
            "house",
            "breed",
            "supplier_name",
            "date_stocked",
            "initial_quantity",
            "initial_age_days",
           
            "status",
            "notes",
        ]

        widgets = {
            "date_stocked": forms.DateInput(attrs={"type": "date"}),
            "initial_age_days": forms.NumberInput(attrs={"type": "number"}),
            "expected_lay_start": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-control"})