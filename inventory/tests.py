from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from .models import InventoryTransaction, Item, ItemCategory, Store, Supplier


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
        self.category = ItemCategory.objects.create(code="FEED", name="Feed")
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

    def test_supplier_page_shows_item_checkboxes(self):
        response = self.client.get(reverse("suppliers"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="products"', html=False)
        self.assertContains(response, "Maize bran")
        self.assertContains(response, "Concentrate")
