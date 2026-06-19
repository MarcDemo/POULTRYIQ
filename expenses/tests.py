from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from expenses.models import ExpenseCategory, ExpenseTransaction
from inventory.models import InventoryTransaction, Item, Supplier


class ExpenseFormFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="expense-user",
            password="pass1234",
        )
        self.client.force_login(self.user)

    def test_expense_form_splits_expense_and_purchase_categories(self):
        response = self.client.get(reverse("expense_form"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "expenses/expense_form.html")
        self.assertTrue(
            all(
                category.expense_type != ExpenseCategory.ExpenseType.COST_OF_REVENUE
                for category in response.context["expense_categories"]
            )
        )
        self.assertTrue(
            all(
                category.expense_type == ExpenseCategory.ExpenseType.COST_OF_REVENUE
                for category in response.context["purchase_categories"]
            )
        )

    def test_purchase_form_records_supplier_and_item_on_expense_transaction(self):
        supplier = Supplier.objects.create(
            name="Kafika",
            phone="0700000000",
            product="Maize bran, Concentrate",
            is_active=True,
        )

        response = self.client.post(
            reverse("expense_form"),
            {
                "type": "purchase",
                "date": date(2026, 6, 19).isoformat(),
                "supplier": str(supplier.pk),
                "inventory_category": "FEED",
                "item": "Maize bran",
                "quantity": "100",
                "unit_price": "2500",
                "description": "Invoice 42",
                "payment_method": ExpenseTransaction.PAYMENT_CASH,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        expense = ExpenseTransaction.objects.get(item_name="Maize bran")

        self.assertEqual(expense.total_amount, Decimal("250000.00"))
        self.assertEqual(expense.category.code, "FEEDS")
        self.assertEqual(expense.item_name, "Maize bran")
        self.assertEqual(expense.quantity, Decimal("100.000"))
        self.assertEqual(expense.unit, "kg")
        self.assertEqual(expense.unit_cost, Decimal("2500.00"))
        self.assertEqual(expense.supplier_name, "Kafika")
        self.assertEqual(expense.supplier_contact, "0700000000")
        self.assertIn("Purchase: Maize bran", expense.description)

        item = Item.objects.get(name="Maize bran")
        stock_in = InventoryTransaction.objects.get(item=item)
        self.assertEqual(item.category.code, "FEED")
        self.assertEqual(stock_in.quantity, Decimal("100.000"))
        self.assertEqual(stock_in.unit_price, Decimal("2500.00"))
        self.assertEqual(stock_in.supplier_name, "Kafika")

    def test_supplier_product_limits_purchase_categories(self):
        Supplier.objects.create(name="Kafika", product="Maize bran", is_active=True)

        response = self.client.get(reverse("expense_form"))
        supplier = Supplier.objects.get(name="Kafika")

        self.assertIn("FEED", response.context["supplier_category_map"][str(supplier.pk)])
        self.assertNotIn("EQUIPMENT", response.context["supplier_category_map"][str(supplier.pk)])
