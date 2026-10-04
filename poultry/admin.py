from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from .bird_ledger import record_batch_stocking, reverse_bird_transfer
from .models import (
    BirdTransfer,
    BirdTransferAllocation,
    HouseBirdMovement,
    PoultryBatch,
    PoultryHouse,
)


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
            "initial_age_days",
            "expected_lay_start",
            "status",
            "notes",
            "created_at",
        ]
        if obj is not None:
            fields.insert(-1, "created_by")
        return fields

    def save_model(self, request, obj, form, change):
        is_new = obj.pk is None
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        if is_new and not obj.origin_batch_id:
            record_batch_stocking(obj, operator=request.user)


class BirdTransferAllocationInline(admin.TabularInline):
    model = BirdTransferAllocation
    extra = 0
    can_delete = False
    readonly_fields = (
        "destination_house",
        "quantity",
        "destination_batch",
        "projected_house_birds",
        "exceeded_capacity",
    )

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(BirdTransfer)
class BirdTransferAdmin(admin.ModelAdmin):
    change_form_template = "admin/poultry/birdtransfer/change_form.html"
    list_display = ("transfer_id", "source_batch", "transfer_date", "status", "created_by", "created_at")
    list_filter = ("status", "transfer_date")
    search_fields = ("source_batch__batch_code", "source_batch__house__house_code", "created_by__username")
    readonly_fields = (
        "source_batch",
        "transfer_date",
        "notes",
        "capacity_warning_acknowledged",
        "status",
        "reversal_of",
        "reversal_reason",
        "created_by",
        "created_at",
    )
    inlines = (BirdTransferAllocationInline,)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        return [
            path(
                "<path:object_id>/reverse-transfer/",
                self.admin_site.admin_view(self.reverse_transfer_view),
                name="poultry_birdtransfer_reverse",
            ),
        ] + super().get_urls()

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = dict(extra_context or {})
        transfer = self.get_object(request, object_id)
        extra_context["can_reverse_transfer"] = bool(
            request.user.is_superuser
            and transfer
            and transfer.status == BirdTransfer.Status.COMPLETED
            and not transfer.reversal_of_id
        )
        extra_context["reverse_transfer_url"] = reverse(
            "admin:poultry_birdtransfer_reverse",
            args=[object_id],
        )
        return super().change_view(request, object_id, form_url, extra_context)

    def reverse_transfer_view(self, request, object_id):
        if not request.user.is_superuser:
            return HttpResponseForbidden("Only Django superusers can reverse bird transfers.")
        transfer = get_object_or_404(BirdTransfer, pk=object_id)
        if request.method == "POST":
            try:
                reverse_bird_transfer(
                    actor=request.user,
                    transfer_id=transfer.pk,
                    reason=request.POST.get("reason", ""),
                )
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
            else:
                messages.success(request, f"Transfer {transfer.pk} was reversed with an immutable audit trail.")
                return redirect("admin:poultry_birdtransfer_change", transfer.pk)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": transfer,
            "title": f"Reverse transfer {transfer.pk}",
        }
        return TemplateResponse(request, "admin/poultry/birdtransfer/reverse.html", context)


@admin.register(HouseBirdMovement)
class HouseBirdMovementAdmin(admin.ModelAdmin):
    list_display = ("movement_id", "occurred_on", "house", "batch", "direction", "quantity", "movement_type")
    list_filter = ("direction", "movement_type", "occurred_on", "house")
    search_fields = ("batch__batch_code", "house__house_code", "notes")
    readonly_fields = tuple(field.name for field in HouseBirdMovement._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False


