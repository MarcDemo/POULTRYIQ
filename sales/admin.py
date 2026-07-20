from django.contrib import admin
from .models import Customer, CustomerPayment, ReceivableLedger, SaleInvoice, SaleItem


@admin.register(SaleItem)
class SaleItemAdmin(admin.ModelAdmin):
    list_display = ("invoice", "product_name", "account", "quantity", "unit_price", "line_total")
    list_filter = ("account__account_type",)
    search_fields = ("invoice__invoice_no", "product_name", "account__code", "account__account_name")
    autocomplete_fields = ("category", "account")


admin.site.register(Customer)
admin.site.register(SaleInvoice)
admin.site.register(ReceivableLedger)
admin.site.register(CustomerPayment)
