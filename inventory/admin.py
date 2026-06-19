from django.contrib import admin

from .models import InventoryRequisition, InventoryTransaction, Item, ItemCategory, ReorderRule, Store, Supplier


@admin.register(InventoryRequisition)
class InventoryRequisitionAdmin(admin.ModelAdmin):
    list_display = ("requested_by", "item", "item_name", "quantity", "unit", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("requested_by__username", "requested_by__first_name", "requested_by__last_name", "item__name", "item_name", "reason")
    readonly_fields = ("created_at", "updated_at")


admin.site.register(Supplier)
admin.site.register(Store)
admin.site.register(ItemCategory)
admin.site.register(Item)
admin.site.register(InventoryTransaction)
admin.site.register(ReorderRule)
