from django.test import TestCase
from django.urls import reverse
from datetime import date
from decimal import Decimal

from accounts.models import Role, User
from accounting.models import AccountingCode
from accounting.services import get_pl_data
from expenses.models import ExpenseAllocation, ExpenseCategory, ExpenseTransaction
from inventory.models import Supplier
from sales.models import Customer, CustomerPayment, ReceivableLedger, SaleInvoice, SaleItem

from .forms import PoultryBatchForm
from .models import ApprovalStatus, FeedRecord, PoultryBatch, PoultryHouse, egg_collection
from .views import _build_investor_builder_data


class PoultryAdminTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="adminuser",
            email="admin@example.com",
            password="StrongPass1",
        )
        self.client.force_login(self.admin_user)

    def test_admin_can_add_poultry_house(self):
        response = self.client.post(
            reverse("admin:poultry_poultryhouse_add"),
            {
                "house_code": "HSE-01",
                "name": "Layer House A",
                "capacity": 1200,
                "is_active": "on",
                "_save": "Save",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(PoultryHouse.objects.filter(house_code="HSE-01").exists())

    def test_admin_can_add_poultry_batch_and_creator_is_auto_assigned(self):
        house = PoultryHouse.objects.create(
            house_code="HSE-02",
            name="Brooder House",
            capacity=800,
        )

        response = self.client.post(
            reverse("admin:poultry_poultrybatch_add"),
            {
                "batch_code": "BATCH-001",
                "house": str(house.pk),
                "breed": "Layers",
                "supplier_name": "Best Chicks Ltd",
                "date_stocked": "2026-04-24",
                "initial_quantity": 500,
                "initial_age_days": 120,
                "expected_lay_start": "2026-09-01",
                "status": PoultryBatch.Status.ACTIVE,
                "notes": "Initial admin registration",
                "_save": "Save",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)

        batch = PoultryBatch.objects.get(batch_code="BATCH-001")
        self.assertEqual(batch.house, house)
        self.assertEqual(batch.created_by, self.admin_user)


class PoultryBatchFormTests(TestCase):
    def test_supplier_choices_only_include_poultry_suppliers(self):
        Supplier.objects.create(name="Kafika Feeds", product="Maize bran, Concentrate", is_active=True)
        Supplier.objects.create(name="Chick Supplier", product="Day-old chicks, Point-of-lay birds", is_active=True)

        form = PoultryBatchForm()
        supplier_values = {value for value, label in form.fields["supplier_name"].choices}

        self.assertIn("Chick Supplier", supplier_values)
        self.assertNotIn("Kafika Feeds", supplier_values)


class AddBatchAccountingTests(TestCase):
    def setUp(self):
        role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Manager")
        self.user = User.objects.create_user(
            username="batch-manager",
            password="pass1234",
            role=role,
        )
        self.client.force_login(self.user)
        self.house = PoultryHouse.objects.create(
            house_code="BATCH-HSE-01",
            name="Batch House",
            capacity=1000,
        )
        Supplier.objects.create(
            name="Chick Supplier",
            product="Day-old chicks",
            is_active=True,
        )

    def test_paid_batch_posts_cost_of_revenue_accounting_code(self):
        response = self.client.post(
            reverse("add_batch"),
            {
                "house": str(self.house.pk),
                "breed": "Layers",
                "supplier_name": "Chick Supplier",
                "initial_quantity": "500",
                "amount_paid": "1250000",
                "date_stocked": "2026-06-19",
                "initial_age_days": "1",
                "notes": "New flock",
            },
        )

        self.assertRedirects(response, reverse("birds"))
        expense = ExpenseTransaction.objects.get(description__icontains="Bird batch purchase")
        self.assertEqual(expense.total_amount, Decimal("1250000.00"))
        self.assertEqual(expense.category.expense_type, ExpenseCategory.ExpenseType.COST_OF_REVENUE)
        self.assertTrue(
            AccountingCode.objects.filter(
                account_type="COST_OF_REVENUE",
                prefix="CRO",
                content_type__model="expensetransaction",
                object_id=expense.pk,
            ).exists()
        )
        self.assertEqual(get_pl_data()["totals"]["cost_of_revenue"], Decimal("1250000.00"))


class SupervisorHouseScopeTests(TestCase):
    def setUp(self):
        self.supervisor_role = Role.objects.create(
            code=Role.RoleCode.SUPERVISOR,
            name="Supervisor",
        )
        self.manager_role = Role.objects.create(
            code=Role.RoleCode.MANAGER,
            name="Manager",
        )

        self.supervisor = User.objects.create_user(
            username="scope-supervisor",
            password="pass1234",
            role=self.supervisor_role,
        )
        self.manager = User.objects.create_user(
            username="scope-manager",
            password="pass1234",
            role=self.manager_role,
        )

        self.house_a = PoultryHouse.objects.create(
            house_code="HSE-SCOPE-A",
            name="Scope House A",
            capacity=400,
        )
        self.house_b = PoultryHouse.objects.create(
            house_code="HSE-SCOPE-B",
            name="Scope House B",
            capacity=400,
        )
        self.supervisor.houses.add(self.house_a)

        self.batch_a = PoultryBatch.objects.create(
            batch_code="SCOPE-BATCH-A",
            house=self.house_a,
            breed="Layers",
            supplier_name="Farm Source",
            date_stocked=date(2026, 5, 1),
            initial_quantity=100,
            initial_age_days=120,
            status=PoultryBatch.Status.ACTIVE,
            created_by=self.manager,
        )
        self.batch_b = PoultryBatch.objects.create(
            batch_code="SCOPE-BATCH-B",
            house=self.house_b,
            breed="Layers",
            supplier_name="Farm Source",
            date_stocked=date(2026, 5, 1),
            initial_quantity=100,
            initial_age_days=120,
            status=PoultryBatch.Status.ACTIVE,
            created_by=self.manager,
        )

        egg_collection.objects.create(
            batch=self.batch_a,
            collection_date=date(2026, 5, 8),
            eggs_collected=80,
            status=ApprovalStatus.PENDING,
            collected_by=self.manager,
        )
        egg_collection.objects.create(
            batch=self.batch_b,
            collection_date=date(2026, 5, 8),
            eggs_collected=95,
            status=ApprovalStatus.PENDING,
            collected_by=self.manager,
        )

    def test_supervisor_approval_list_only_contains_assigned_house_records(self):
        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("supapproval"))

        pending_eggs = list(response.context["pending_eggs"])
        self.assertEqual(len(pending_eggs), 1)
        self.assertEqual(pending_eggs[0].batch.house, self.house_a)
        self.assertEqual(response.context["pending_eggs_count"], 1)
        assigned_houses = list(response.context["assigned_houses"])
        self.assertEqual(len(assigned_houses), 1)
        self.assertEqual(assigned_houses[0], self.house_a)
        self.assertEqual(response.context["assigned_houses_count"], 1)
        self.assertContains(response, "Scope House A")
        self.assertNotContains(response, "Scope House B")

    def test_supervisor_dashboard_shows_only_assigned_house_badges_and_counts(self):
        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("supdash"))

        assigned_houses = list(response.context["assigned_houses"])
        self.assertEqual(len(assigned_houses), 1)
        self.assertEqual(assigned_houses[0], self.house_a)
        self.assertEqual(response.context["assigned_houses_count"], 1)
        self.assertEqual(response.context["pending_eggs"], 1)
        self.assertEqual(response.context["total_pending"], 1)


class InvestorFinancialDashboardTests(TestCase):
    def setUp(self):
        self.manager_role = Role.objects.create(
            code=Role.RoleCode.MANAGER,
            name="Manager",
        )
        self.user = User.objects.create_user(
            username="investor-manager",
            password="StrongPass1",
            role=self.manager_role,
        )
        self.client.force_login(self.user)
        self.house = PoultryHouse.objects.create(
            house_code="INV-HSE-01",
            name="Investor House",
            capacity=500,
        )
        self.batch = PoultryBatch.objects.create(
            batch_code="INV-BATCH-01",
            house=self.house,
            breed="Layers",
            supplier_name="Supplier",
            amount_paid=Decimal("100000.00"),
            date_stocked=date(2026, 1, 1),
            initial_quantity=200,
            initial_age_days=120,
            status=PoultryBatch.Status.ACTIVE,
            created_by=self.user,
        )
        self.customer = Customer.objects.create(name="Prime Buyer")
        self.feed_category, _ = ExpenseCategory.objects.get_or_create(
            code="FEED",
            defaults={"name": "Feed"},
        )
        self.labour_category, _ = ExpenseCategory.objects.get_or_create(
            code="LABOUR",
            defaults={"name": "Labour"},
        )

    def _invoice(self, total=Decimal("1000.00"), paid=Decimal("800.00")):
        invoice = SaleInvoice.objects.create(
            invoice_no=f"INV-{SaleInvoice.objects.count() + 1:04d}",
            customer=self.customer,
            invoice_date=date(2026, 1, 10),
            due_date=date(2026, 1, 20),
            subtotal=total,
            total_amount=total,
            status=SaleInvoice.Status.ISSUED,
            created_by=self.user,
        )
        SaleItem.objects.create(
            invoice=invoice,
            batch=self.batch,
            product_name="Eggs",
            quantity=Decimal("10.000"),
            unit="trays",
            unit_price=total / Decimal("10"),
            line_total=total,
        )
        if paid:
            CustomerPayment.objects.create(
                invoice=invoice,
                customer=self.customer,
                payment_date=date(2026, 1, 12),
                method=CustomerPayment.Method.CASH,
                amount=paid,
                received_by=self.user,
            )
        ReceivableLedger.objects.create(
            invoice=invoice,
            amount_due=total,
            amount_paid=paid,
            balance=total - paid,
        )
        return invoice

    def _expense(self, amount, category=None):
        return ExpenseTransaction.objects.create(
            expense_date=date(2026, 1, 11),
            category=category or self.feed_category,
            description="Investor test expense",
            total_amount=amount,
            period_year=2026,
            period_month=1,
            status=ExpenseTransaction.Status.APPROVED,
            created_by=self.user,
        )

    def _approved_eggs(self, eggs=100, rejected=5):
        return egg_collection.objects.create(
            batch=self.batch,
            collection_date=date(2026, 1, 11),
            eggs_collected=eggs,
            eggs_rejected=rejected,
            status=ApprovalStatus.APPROVED,
            collected_by=self.user,
        )

    def test_profitable_period_calculates_core_investor_metrics(self):
        self._invoice(total=Decimal("1000.00"), paid=Decimal("800.00"))
        self._expense(Decimal("300.00"))
        self._approved_eggs(eggs=120, rejected=5)
        FeedRecord.objects.create(
            batch=self.batch,
            record_date=date(2026, 1, 11),
            feed_type=FeedRecord.FeedType.LAYER_MASH,
            quantity_kg=Decimal("20.00"),
            status=ApprovalStatus.APPROVED,
            recorded_by=self.user,
        )

        response = self.client.get(reverse("investor"), {
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
        })

        metrics = response.context["metrics"]
        self.assertEqual(metrics["net_profit"], Decimal("700.00"))
        self.assertEqual(metrics["unpaid_receivables"], Decimal("200.00"))
        self.assertEqual(metrics["collection_rate"], Decimal("80.0"))
        self.assertAlmostEqual(metrics["cost_per_egg"], Decimal("2.61"), places=2)
        self.assertEqual(response.context["financial_health"]["label"], "Profitable")

    def test_loss_period_with_zero_eggs_does_not_divide_by_zero(self):
        self._expense(Decimal("500.00"))

        response = self.client.get(reverse("investor"), {
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
        })

        metrics = response.context["metrics"]
        self.assertEqual(metrics["net_profit"], Decimal("-500.00"))
        self.assertEqual(metrics["cost_per_egg"], Decimal("0"))
        self.assertEqual(metrics["feed_per_egg"], Decimal("0"))
        self.assertEqual(response.context["financial_health"]["label"], "Loss-Making")

    def test_high_expense_ratio_is_flagged(self):
        self._invoice(total=Decimal("1000.00"), paid=Decimal("1000.00"))
        self._expense(Decimal("800.00"))
        self._approved_eggs(eggs=120, rejected=0)

        response = self.client.get(reverse("investor"), {
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
        })

        self.assertEqual(response.context["metrics"]["expense_ratio"], Decimal("80.0"))
        self.assertEqual(response.context["financial_health"]["label"], "High Cost")

    def test_batch_profitability_uses_linked_sales_and_allocations(self):
        self._invoice(total=Decimal("1000.00"), paid=Decimal("1000.00"))
        expense = self._expense(Decimal("400.00"))
        ExpenseAllocation.objects.create(
            expense=expense,
            batch=self.batch,
            method=ExpenseAllocation.Method.DIRECT,
            amount_allocated=Decimal("350.00"),
        )

        response = self.client.get(reverse("investor"), {
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
        })

        batch_row = response.context["batch_profitability"][0]
        self.assertEqual(batch_row["label"], "INV-BATCH-01")
        self.assertEqual(batch_row["revenue"], Decimal("1000.00"))
        self.assertEqual(batch_row["expenses"], Decimal("350.00"))
        self.assertEqual(batch_row["profit"], Decimal("650.00"))

    def test_builder_data_exposes_financial_questions_and_indicators(self):
        builder_data = _build_investor_builder_data(date(2026, 1, 1), date(2026, 1, 31), "month")
        preset_labels = {preset["label"] for preset in builder_data["presets"]}
        indicator_keys = {indicator["key"] for indicator in builder_data["indicators"]}
        theme_labels = {theme["label"] for theme in builder_data["themes"]}

        self.assertIn("Why am I making a loss?", preset_labels)
        self.assertIn("Which costs are too high?", preset_labels)
        self.assertIn("Cost per saleable egg", {indicator["label"] for indicator in builder_data["indicators"]})
        self.assertTrue({"cost_per_egg", "batch_profit", "feed_cost", "labour_cost"}.issubset(indicator_keys))
        self.assertTrue({"Profitability", "Cash Flow", "Cost of Production"}.issubset(theme_labels))
