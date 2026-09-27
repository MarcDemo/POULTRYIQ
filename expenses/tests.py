from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounting.services import get_bs_data
from expenses.models import ExpenseCategory, ExpenseTransaction
from inventory.models import InventoryTransaction, Item, Supplier
from accounts.models import Role


class ExpenseFormFlowTests(TestCase):
    def setUp(self):
        manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Farm Manager")
        self.user = get_user_model().objects.create_user(
            username="expense-user",
            password="pass1234",
            role=manager_role,
        )
        self.client.force_login(self.user)

    def test_expense_form_splits_expense_and_purchase_categories(self):
        response = self.client.get(reverse("expense_form"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "expenses/expense_form.html")
        self.assertTrue(
            all(
                category.expense_type == ExpenseCategory.ExpenseType.MONTHLY_EXPENSES
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

        bs_data = get_bs_data()
        assets_group = next(row for row in bs_data["grouped_accounts"] if row["group_value"] == "ASSETS")
        current_assets = next(row for row in assets_group["type_groups"] if row["type_value"] == "CURRENT_ASSET")
        inventory_account = next(row for row in current_assets["accounts"] if row["code"] == "321001")
        self.assertEqual(inventory_account["account_name"], "Inventory Purchases")
        self.assertEqual(inventory_account["amount"], Decimal("250000.00000"))
        self.assertEqual(current_assets["type_total"], Decimal("250000.00000"))

    def test_supplier_product_limits_purchase_categories(self):
        Supplier.objects.create(name="Kafika", product="Maize bran", is_active=True)

        response = self.client.get(reverse("expense_form"))
        supplier = Supplier.objects.get(name="Kafika")

        self.assertIn("FEED", response.context["supplier_category_map"][str(supplier.pk)])
        self.assertNotIn("EQUIPMENT", response.context["supplier_category_map"][str(supplier.pk)])

    def test_purchase_catalog_excludes_fixed_asset_equipment(self):
        response = self.client.get(reverse("expense_form"))

        self.assertNotIn("EQUIPMENT", [category["code"] for category in response.context["inventory_categories"]])
        self.assertNotIn("EQUIPMENT", response.context["items_by_category"])

    def test_purchase_form_rejects_fixed_asset_equipment_items(self):
        supplier = Supplier.objects.create(
            name="Equipment Supplier",
            phone="0700000000",
            product="Feeders",
            is_active=True,
        )

        response = self.client.post(
            reverse("expense_form"),
            {
                "type": "purchase",
                "date": date(2026, 6, 19).isoformat(),
                "supplier": str(supplier.pk),
                "inventory_category": "EQUIPMENT",
                "item": "Feeders",
                "quantity": "2",
                "unit_price": "50000",
                "description": "Should be fixed asset",
                "payment_method": ExpenseTransaction.PAYMENT_CASH,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid purchase category selected.")
        self.assertContains(response, "Invalid item selected.")
        self.assertFalse(ExpenseTransaction.objects.filter(item_name="Feeders").exists())
        self.assertFalse(InventoryTransaction.objects.filter(item__name="Feeders").exists())

    def test_rent_expense_requires_and_records_prepayment_period(self):
        rent_category = ExpenseCategory.objects.create(
            code="RENT",
            name="Rent",
            expense_type=ExpenseCategory.ExpenseType.MONTHLY_EXPENSES,
        )

        response = self.client.post(
            reverse("expense_form"),
            {
                "type": "expense",
                "date": "2026-07-01",
                "category": str(rent_category.pk),
                "description": "July office rent",
                "amount": "31000",
                "payment_method": ExpenseTransaction.PAYMENT_CASH,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please provide the prepayment start date.")
        self.assertFalse(ExpenseTransaction.objects.filter(category=rent_category).exists())

        response = self.client.post(
            reverse("expense_form"),
            {
                "type": "expense",
                "date": "2026-07-01",
                "category": str(rent_category.pk),
                "description": "July office rent",
                "amount": "31000",
                "payment_method": ExpenseTransaction.PAYMENT_CASH,
                "prepayment_start_date": "2026-07-01",
                "prepayment_end_date": "2026-07-31",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        expense = ExpenseTransaction.objects.get(category=rent_category)
        self.assertTrue(expense.is_prepayment)
        self.assertEqual(expense.prepayment_type, ExpenseTransaction.PrepaymentType.RENT)
        self.assertEqual(expense.prepayment_start_date.isoformat(), "2026-07-01")
        self.assertEqual(expense.prepayment_end_date.isoformat(), "2026-07-31")

        bs_data = get_bs_data(end_date=date(2026, 7, 15))
        assets_group = next(row for row in bs_data["grouped_accounts"] if row["group_value"] == "ASSETS")
        prepayments = next(row for row in assets_group["type_groups"] if row["type_value"] == "PREPAYMENT")
        prepaid_rent = next(row for row in prepayments["accounts"] if row["code"] == "341003")
        self.assertEqual(prepaid_rent["amount"], Decimal("17000.00"))
