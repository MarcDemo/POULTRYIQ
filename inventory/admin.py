from django.contrib import admin

from .models import InventoryRequisition, InventoryTransaction, Item, ItemCategory, ReorderRule, Store, Supplier, SupplierProduct


@admin.register(InventoryRequisition)
class InventoryRequisitionAdmin(admin.ModelAdmin):
    list_display = ("requested_by", "item", "item_name", "quantity", "unit", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("requested_by__username", "requested_by__first_name", "requested_by__last_name", "item__name", "item_name", "reason")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "location", "preferred_payment_method", "other_supplied_products", "is_active")
    search_fields = ("name", "tin_number", "phone", "location", "supplied_products__name", "other_supplied_products")
    list_filter = ("preferred_payment_method", "is_active", "supplied_products__account")
    filter_horizontal = ("supplied_products",)


@admin.register(SupplierProduct)
class SupplierProductAdmin(admin.ModelAdmin):
    list_display = ("name", "account", "unit", "is_active")
    list_filter = ("is_active", "account__account_type", "account")
    search_fields = ("name", "account__code", "account__account_name")
    autocomplete_fields = ("account",)


admin.site.register(Store)
admin.site.register(ItemCategory)
admin.site.register(Item)
admin.site.register(InventoryTransaction)
admin.site.register(ReorderRule)
