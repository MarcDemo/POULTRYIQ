from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import AccountingCode
from accounting.services import get_pl_data
from sales.models import Customer, SaleInvoice, SaleItem


class ProfitAndLossDataTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="accounting-user",
            password="pass1234",
        )
        self.customer = Customer.objects.create(name="P&L Buyer")

    def _create_sale_item(self, invoice_no, line_total):
        invoice = SaleInvoice.objects.create(
            invoice_no=invoice_no,
            customer=self.customer,
            invoice_date=date(2026, 6, 19),
            subtotal=line_total,
            discount_amount=Decimal("0.00"),
            total_amount=line_total,
            status=SaleInvoice.Status.PAID,
            delivery_status=SaleInvoice.DeliveryStatus.DELIVERED,
            created_by=self.user,
        )
        return SaleItem.objects.create(
            invoice=invoice,
            product_name="Eggs",
            quantity=Decimal("2.000"),
            unit="trays",
            unit_price=(line_total / Decimal("2.000")).quantize(Decimal("0.01")),
            line_total=line_total,
        )

    def test_sale_item_line_total_is_used_as_revenue_amount(self):
        item = self._create_sale_item("INV-2026-PL01", Decimal("10000.00"))
        AccountingCode.create_or_get_accounting_code(
            prefix="SE",
            account_type="REVENUE",
            account_name="sale_of_eggs",
            content_object=item,
        )

        pl_data = get_pl_data()

        self.assertEqual(pl_data["revenue"][0]["amount"], Decimal("10000.00"))
        self.assertEqual(pl_data["totals"]["revenue"], Decimal("10000.00"))

    def test_transaction_backed_codes_are_not_reused_by_account_name(self):
        first_item = self._create_sale_item("INV-2026-PL02", Decimal("10000.00"))
        second_item = self._create_sale_item("INV-2026-PL03", Decimal("15000.00"))

        first_code = AccountingCode.create_or_get_accounting_code(
            prefix="SE",
            account_type="REVENUE",
            account_name="sale_of_eggs",
            content_object=first_item,
        )
        second_code = AccountingCode.create_or_get_accounting_code(
            prefix="SE",
            account_type="REVENUE",
            account_name="sale_of_eggs",
            content_object=second_item,
        )

        self.assertNotEqual(first_code.pk, second_code.pk)
        self.assertEqual(AccountingCode.objects.filter(account_name="sale_of_eggs").count(), 2)
        self.assertEqual(get_pl_data()["totals"]["revenue"], Decimal("25000.00"))
