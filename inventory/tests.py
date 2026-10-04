from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from alerts.models import Alert
from expenses.models import ExpenseTransaction
from .models import InventoryRequisition, InventoryTransaction, Item, ItemCategory, Store, Supplier


class StoreOutTests(TestCase):
    def setUp(self):
        role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Farm Manager")
        self.user = User.objects.create_user(
            username="stock-manager",
            password="Pass1234!",
            role=role,
        )
        self.client.force_login(self.user)

        self.store = Store.objects.create(name="Main Store")
        self.category, _ = ItemCategory.objects.get_or_create(code="FEED", defaults={"name": "Feed"})
        self.item = Item.objects.create(
            name="Layer Feed",
            category=self.category,
            unit="kg",
        )
        InventoryTransaction.objects.create(
            tx_date=date.today(),
            tx_type=InventoryTransaction.TxType.IN_,
            store=self.store,
            item=self.item,
            quantity=Decimal("100.000"),
            unit_price=Decimal("20.00"),
            created_by=self.user,
        )

    def test_store_page_records_stock_out(self):
        response = self.client.post(
            reverse("store"),
            {
                "tx_date": date.today().isoformat(),
                "item": str(self.item.pk),
                "quantity": "25",
                "reference": "Farm hand",
                "notes": "Issued for feeding",
            },
        )

        self.assertRedirects(response, reverse("store"))
        stock_out = InventoryTransaction.objects.get(tx_type=InventoryTransaction.TxType.OUT)
        self.assertEqual(stock_out.item, self.item)
        self.assertEqual(stock_out.quantity, Decimal("25.000"))
        self.assertEqual(stock_out.reference, "Farm hand")

    def test_supervisor_can_access_store_and_record_stock_out(self):
        supervisor_role = Role.objects.create(
            code=Role.RoleCode.SUPERVISOR,
            name="Farm Supervisor",
        )
        supervisor = User.objects.create_user(
            username="stock-supervisor",
            password="test-password",
            role=supervisor_role,
        )
        self.client.force_login(supervisor)

        response = self.client.get(reverse("store"))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse("store"),
            {
                "tx_date": date.today().isoformat(),
                "item": str(self.item.pk),
                "quantity": "10",
                "reference": "Farm hand",
                "notes": "Issued by supervisor",
            },
        )

        self.assertRedirects(response, reverse("store"))
        stock_out = InventoryTransaction.objects.get(
            tx_type=InventoryTransaction.TxType.OUT,
            created_by=supervisor,
        )
        self.assertEqual(stock_out.item, self.item)
        self.assertEqual(stock_out.quantity, Decimal("10.000"))

    def test_store_page_blocks_more_than_available(self):
        response = self.client.post(
            reverse("store"),
            {
                "tx_date": date.today().isoformat(),
                "item": str(self.item.pk),
                "quantity": "150",
                "reference": "Farm hand",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Only 100.000 kg is available for Layer Feed.")
        self.assertFalse(
            InventoryTransaction.objects.filter(tx_type=InventoryTransaction.TxType.OUT).exists()
        )

    def test_inventory_status_shows_label_and_percentage(self):
        InventoryTransaction.objects.create(
            tx_date=date.today(),
            tx_type=InventoryTransaction.TxType.OUT,
            store=self.store,
            item=self.item,
            quantity=Decimal("40.000"),
            created_by=self.user,
        )

        response = self.client.get(reverse("inventory_management"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Moderate")
        self.assertContains(response, "60.000")
        self.assertContains(response, "60% left")

    def test_inventory_purchase_saves_credit_terms(self):
        Supplier.objects.create(
            name="Kafika",
            phone="0700000000",
            product="Concentrate",
            preferred_payment_method=Supplier.PaymentMethod.CREDIT,
            credit_repayment_plan="Pay after flock sales",
            credit_paid_upfront=Decimal("50.00"),
            credit_grace_period_days=30,
            credit_period="30 days",
        )

        response = self.client.post(
            reverse("inventory_management"),
            {
                "item": "Concentrate",
                "category": str(self.category.pk),
                "quantity": "10",
                "unit": "kg",
                "supplier_name": "Kafika",
                "unit_price": "1200",
                "payment_method": "Credit",
                "credit_repayment_plan": "Pay after egg sales",
                "credit_paid_upfront": "60",
                "credit_grace_period_days": "14",
                "credit_period": "14 days",
            },
        )

        self.assertRedirects(response, reverse("inventory_management"))
        tx = InventoryTransaction.objects.get(item__name="Concentrate")
        self.assertEqual(tx.payment_method, InventoryTransaction.PaymentMethod.CREDIT)
        self.assertEqual(tx.credit_repayment_plan, "Pay after egg sales")
        self.assertEqual(tx.credit_paid_upfront, Decimal("60.00"))
        self.assertEqual(tx.credit_grace_period_days, 14)
        self.assertEqual(tx.credit_due_date, date.today() + timedelta(days=14))
        self.assertEqual(tx.credit_period, "14 days")
        expense = ExpenseTransaction.objects.get(supplier_name="Kafika")
        self.assertEqual(expense.payment_method, ExpenseTransaction.PAYMENT_CREDIT)
        self.assertIn("Pay after egg sales", expense.notes)
        self.assertIn("60", expense.notes)
        alert = Alert.objects.get(title="Credit payment due: Kafika")
        self.assertEqual(alert.receiver, self.user)
        self.assertEqual(alert.due_date.date(), tx.credit_due_date)
        self.assertIn("60% was marked as paid upfront", alert.message)
        self.assertIn("4,800.00", alert.message)

    def test_inventory_purchase_saves_momo_receiving_number(self):
        Supplier.objects.create(
            name="MoMo Feeds",
            phone="0700000002",
            product="Concentrate",
            preferred_payment_method=Supplier.PaymentMethod.MOBILE_MONEY,
            momo_receiving_number="0777123456",
        )

        response = self.client.post(
            reverse("inventory_management"),
            {
                "item": "Concentrate",
                "category": str(self.category.pk),
                "quantity": "10",
                "unit": "kg",
                "supplier_name": "MoMo Feeds",
                "unit_price": "1200",
                "payment_method": "Mobile Money",
                "momo_receiving_number": "0777000000",
            },
        )

        self.assertRedirects(response, reverse("inventory_management"))
        tx = InventoryTransaction.objects.get(item__name="Concentrate")
        self.assertEqual(tx.payment_method, InventoryTransaction.PaymentMethod.MOBILE_MONEY)
        self.assertEqual(tx.momo_receiving_number, "0777000000")
        self.assertEqual(tx.bank_account_number, "")
        expense = ExpenseTransaction.objects.get(supplier_name="MoMo Feeds")
        self.assertEqual(expense.payment_method, ExpenseTransaction.PAYMENT_MOBILE)
        self.assertIn("0777000000", expense.notes)

    def test_inventory_purchase_hides_fixed_asset_equipment_category(self):
        equipment = ItemCategory.objects.create(code="EQUIPMENT", name="Equipment")

        response = self.client.get(reverse("inventory_management"))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(equipment, list(response.context["categories"]))

    def test_inventory_purchase_rejects_fixed_asset_equipment_category(self):
        equipment = ItemCategory.objects.create(code="EQUIPMENT", name="Equipment")

        response = self.client.post(
            reverse("inventory_management"),
            {
                "item": "Feeders",
                "category": str(equipment.pk),
                "quantity": "2",
                "unit": "piece",
                "unit_price": "50000",
                "payment_method": "Cash",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fixed assets must be recorded in the Fixed Assets page.")
        self.assertFalse(Item.objects.filter(name="Feeders").exists())
        self.assertFalse(InventoryTransaction.objects.filter(item__name="Feeders").exists())


class InventoryRequisitionTests(TestCase):
    def setUp(self):
        supervisor_role = Role.objects.create(
            code=Role.RoleCode.SUPERVISOR,
            name="Farm Supervisor",
        )
        manager_role = Role.objects.create(
            code=Role.RoleCode.MANAGER,
            name="Farm Manager",
        )
        self.supervisor = User.objects.create_user(
            username="requisition-supervisor",
            password="test-password",
            role=supervisor_role,
        )
        self.manager = User.objects.create_user(
            username="requisition-manager",
            password="test-password",
            role=manager_role,
        )

    def test_unit_price_total_and_requisition_histories(self):
        self.client.force_login(self.supervisor)
        response = self.client.post(
            reverse("supervisor_requisitions"),
            {
                "item_name": "Layer Feed",
                "quantity": "2.5",
                "unit_price": "12.50",
                "unit": "kg",
                "reason": "Feed stock is low.",
            },
        )

        self.assertRedirects(response, reverse("supervisor_requisitions"))
        requisition = InventoryRequisition.objects.get(requested_by=self.supervisor)
        self.assertEqual(requisition.unit_price, Decimal("12.50"))
        self.assertEqual(requisition.total_amount, Decimal("31.25"))

        self.client.force_login(self.manager)
        response = self.client.get(reverse("manager_requisitions"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "12.50")
        self.assertContains(response, "31.25")

        response = self.client.post(
            reverse("manager_review_requisition", args=[requisition.pk]),
            {"action": "approve", "notes": "Approved."},
        )
        self.assertRedirects(response, reverse("manager_requisitions"))
        response = self.client.get(reverse("manager_requisitions"))
        self.assertContains(response, "Reviewed Requisition History")
        self.assertContains(response, "Approved")
        self.assertContains(response, "31.25")

        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("supervisor_requisitions"))
        self.assertContains(response, "My Requisitions")
        self.assertContains(response, "31.25")

    def test_unit_price_is_required(self):
        self.client.force_login(self.supervisor)

        response = self.client.post(
            reverse("supervisor_requisitions"),
            {
                "item_name": "Layer Feed",
                "quantity": "2.5",
                "unit": "kg",
                "reason": "Feed stock is low.",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(InventoryRequisition.objects.exists())


class SupplierPageTests(TestCase):
    def setUp(self):
        role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Farm Manager")
        self.user = User.objects.create_user(
            username="supplier-manager",
            password="Pass1234!",
            role=role,
        )
        self.client.force_login(self.user)

    def test_supplier_form_saves_selected_supplied_items(self):
        response = self.client.post(
            reverse("suppliers"),
            {
                "name": "Kafika",
                "phone": "0700000000",
                "products": ["Maize bran", "Concentrate"],
            },
        )

        self.assertRedirects(response, reverse("suppliers"))
        supplier = Supplier.objects.get(name="Kafika")
        self.assertEqual(supplier.product, "Concentrate, Maize bran")

    def test_supplier_form_saves_credit_payment_terms(self):
        response = self.client.post(
            reverse("suppliers"),
            {
                "name": "Credit Feeds",
                "phone": "0700000001",
                "preferred_payment_method": "Credit",
                "credit_repayment_plan": "Pay 50% weekly until cleared",
                "credit_paid_upfront": "50",
                "credit_grace_period_days": "45",
                "credit_period": "45 days",
                "products": ["Concentrate"],
            },
        )

        self.assertRedirects(response, reverse("suppliers"))
        supplier = Supplier.objects.get(name="Credit Feeds")
        self.assertEqual(supplier.preferred_payment_method, Supplier.PaymentMethod.CREDIT)
        self.assertEqual(supplier.credit_repayment_plan, "Pay 50% weekly until cleared")
        self.assertEqual(supplier.credit_paid_upfront, Decimal("50.00"))
        self.assertEqual(supplier.credit_grace_period_days, 45)
        self.assertEqual(supplier.credit_period, "45 days")

    def test_supplier_form_saves_bank_account_number(self):
        response = self.client.post(
            reverse("suppliers"),
            {
                "name": "Bank Feeds",
                "phone": "0700000003",
                "preferred_payment_method": "Bank",
                "bank_account_number": "1234567890",
                "products": ["Concentrate"],
            },
        )

        self.assertRedirects(response, reverse("suppliers"))
        supplier = Supplier.objects.get(name="Bank Feeds")
        self.assertEqual(supplier.preferred_payment_method, Supplier.PaymentMethod.BANK)
        self.assertEqual(supplier.bank_account_number, "1234567890")
        self.assertEqual(supplier.momo_receiving_number, "")
        self.assertEqual(supplier.credit_repayment_plan, "")

    def test_supplier_page_shows_item_checkboxes(self):
        response = self.client.get(reverse("suppliers"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="products"', html=False)
        self.assertContains(response, "Maize bran")
        self.assertContains(response, "Concentrate")
