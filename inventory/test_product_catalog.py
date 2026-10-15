from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from accounting.models import ChartOfAccount
from inventory.models import Supplier, SupplierProduct
from inventory.views import supplier_visible_products


catalog = import_module("inventory.migrations.0017_seed_feed_and_health_products")


class ReferenceProductCatalogTests(TestCase):
    def seed(self):
        catalog.seed_products(apps, SimpleNamespace(connection=connection))

    def test_all_reference_products_are_visible_under_cost_of_revenue(self):
        for system_code, rows in catalog.PRODUCTS.items():
            for name, unit, aliases in rows:
                with self.subTest(product=name):
                    product = supplier_visible_products().get(name=name)
                    self.assertEqual(product.account.system_code, system_code)
                    self.assertEqual(product.account.account_type.legacy_code, "COST_OF_REVENUE")
                    self.assertEqual(product.unit, unit)

    def test_repeated_seed_reuses_alias_and_preserves_supplier_selection(self):
        feed = ChartOfAccount.objects.get(system_code="FEEDS_COST")
        product = SupplierProduct.objects.get(name="Layer concentrate 5%")
        product.name = "layer conc 5%"
        product.save()
        vaccine = SupplierProduct.objects.get(name="NCD plain")
        vaccine.account = feed
        vaccine.save()
        supplier = Supplier.objects.create(name="Reference supplier")
        supplier.supplied_products.add(product, vaccine)
        count = SupplierProduct.objects.count()

        self.seed()
        self.seed()

        self.assertEqual(SupplierProduct.objects.count(), count)
        self.assertEqual(supplier.supplied_products.count(), 2)
        vaccine.refresh_from_db()
        self.assertEqual(vaccine.account.system_code, "VACCINATIONS_COST")
        self.assertTrue(SupplierProduct.objects.filter(pk=product.pk, name="layer conc 5%").exists())

    def test_supplier_form_lists_reference_products_by_account(self):
        role, _ = Role.objects.get_or_create(code="MANAGER", defaults={"name": "Manager"})
        user = User.objects.create_user(username="catalog-manager", password="test-only", role=role)
        self.client.force_login(user)
        response = self.client.get(reverse("suppliers"))
        self.assertEqual(response.status_code, 200)
        for name in ("Layer concentrate 5%", "Sunflower meal", "NCD plain", "Dewormer"):
            self.assertContains(response, name)
        groups = {group["account"].system_code: group["products"] for group in response.context["product_groups"]}
        self.assertIn(SupplierProduct.objects.get(name="NCD plain"), groups["VACCINATIONS_COST"])
