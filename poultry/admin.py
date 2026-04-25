from django.contrib import admin

from .models import PoultryBatch, PoultryHouse


@admin.register(PoultryHouse)
class PoultryHouseAdmin(admin.ModelAdmin):
    list_display = ("house_code", "name", "capacity", "is_active", "batch_count", "created_at")
    list_filter = ("is_active",)
    search_fields = ("house_code", "name")
    ordering = ("house_code",)
    readonly_fields = ("created_at",)

    fieldsets = (
        (
            None,
            {
                "fields": ("house_code", "name", "capacity", "is_active"),
            },
        ),
        ("Audit", {"fields": ("created_at",)}),
    )

    @admin.display(description="Batches")
    def batch_count(self, obj):
        return obj.batches.count()


@admin.register(PoultryBatch)
class PoultryBatchAdmin(admin.ModelAdmin):
    list_display = (
        
        "house",
        "breed",
        "date_stocked",
        "initial_quantity",
        "status",
        "created_by",
        "created_at",
    )
    list_filter = ("status", "house", "date_stocked", "created_at")
    search_fields = (
        "batch_code",
        "breed",
        "supplier_name",
        "house__house_code",
        "house__name",
        "created_by__username",
    )
    ordering = ("-date_stocked", "batch_code")
    date_hierarchy = "date_stocked"
    autocomplete_fields = ("house",)
    readonly_fields = ("created_by", "created_at")
    list_select_related = ("house", "created_by")

    def get_fields(self, request, obj=None):
        fields = [
            "batch_code",
            "house",
            "breed",
            "supplier_name",
            "date_stocked",
            "initial_quantity",
            "expected_lay_start",
            "status",
            "notes",
            "created_at",
        ]
        if obj is not None:
            fields.insert(-1, "created_by")
        return fields

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


