import json

from django.db import connection
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.db.models import Sum
from datetime import date
from decimal import Decimal

from accounts.models import Role, User
from accounting.models import AccountingCode
from accounting.services import get_pl_data
from expenses.models import ExpenseAllocation, ExpenseCategory, ExpenseTransaction
from inventory.models import InventoryTransaction, Item, Store, Supplier
from sales.models import Customer, CustomerPayment, ReceivableLedger, SaleInvoice, SaleItem

from .forms import PoultryBatchForm
from .models import (
    ApprovalStatus,
    FeedFormulaTemplate,
    FeedMixture,
    FeedMixtureAllocation,
    FeedRecord,
    FlockStage,
    InvestorKpiTarget,
    BirdTransfer,
    HouseBirdMovement,
    MortalityCause,
    MortalityRecord,
    PoultryBatch,
    PoultryHouse,
    egg_collection,
    flock_stage_for_age_days,
)
from .bird_ledger import (
    batch_bird_count,
    create_bird_transfer,
    house_bird_count,
    record_delivered_bird_sale,
    reverse_bird_transfer,
)
from .services.investor_analysis import (
    GUIDED_QUESTIONS,
    METRIC_CATALOG,
    TARGET_DIRECTIONS,
    active_targets,
    create_target_version,
    previous_period,
    target_payload,
)
from .units import format_eggs_as_trays
from .views import _build_investor_builder_data, scale_formula_ingredients


class SupervisorFieldOperationsTests(TestCase):
    def setUp(self):
        supervisor_role = Role.objects.create(
            code=Role.RoleCode.SUPERVISOR,
            name="Field Supervisor",
        )
        manager_role = Role.objects.create(
            code=Role.RoleCode.MANAGER,
            name="Field Manager",
        )
        self.supervisor = User.objects.create_user(
            username="field-supervisor",
            password="pass1234",
            role=supervisor_role,
        )
        self.manager = User.objects.create_user(
            username="field-manager",
            password="pass1234",
            role=manager_role,
        )
        self.assigned_house = PoultryHouse.objects.create(
            house_code="FIELD-A",
            name="Assigned House",
            capacity=500,
        )
        self.other_house = PoultryHouse.objects.create(
            house_code="FIELD-B",
            name="Other House",
            capacity=500,
        )
        self.supervisor.houses.add(self.assigned_house)
        self.assigned_batch = PoultryBatch.objects.create(
            batch_code="FIELD-BATCH-A",
            house=self.assigned_house,
            breed="Layers",
            supplier_name="Farm Source",
            date_stocked=date.today(),
            initial_quantity=200,
            initial_age_days=120,
            status=PoultryBatch.Status.ACTIVE,
            created_by=self.manager,
        )
        PoultryBatch.objects.create(
            batch_code="FIELD-BATCH-B",
            house=self.other_house,
            breed="Layers",
            supplier_name="Farm Source",
            date_stocked=date.today(),
            initial_quantity=200,
            initial_age_days=120,
            status=PoultryBatch.Status.ACTIVE,
            created_by=self.manager,
        )
        self.client.force_login(self.supervisor)

    def test_supervisor_dashboard_exposes_worker_recording_actions(self):
        response = self.client.get(reverse("supdash"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("record_egg"))
        self.assertContains(response, reverse("record_feed"))
        self.assertContains(response, reverse("record_cleaning"))
        self.assertContains(response, reverse("mortality"))
        self.assertContains(response, reverse("sickbay"))

    def test_supervisor_can_open_field_forms_for_assigned_houses_only(self):
        for url_name in ("record_egg", "record_feed", "record_cleaning"):
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)
                self.assertQuerySetEqual(
                    response.context["batches"],
                    [self.assigned_batch],
                )

    def test_supervisor_egg_submission_returns_to_supervisor_dashboard(self):
        response = self.client.post(
            reverse("record_egg"),
            {
                "batch": str(self.assigned_batch.pk),
                "total_eggs": "30",
                "broken_eggs": "1",
                "notes": "Supervisor morning collection",
            },
        )

        self.assertRedirects(response, reverse("supdash"))
        record = egg_collection.objects.get()
        self.assertEqual(record.collected_by, self.supervisor)
        self.assertEqual(record.status, ApprovalStatus.PENDING)


class BirdTransferLedgerTests(TestCase):
    def setUp(self):
        self.supervisor_role = Role.objects.create(code=Role.RoleCode.SUPERVISOR, name="Transfer Supervisor")
        self.manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Transfer Manager")
        self.supervisor = User.objects.create_user(
            username="transfer-supervisor",
            password="pass1234",
            role=self.supervisor_role,
        )
        self.manager = User.objects.create_user(
            username="transfer-manager",
            password="pass1234",
            role=self.manager_role,
        )
        self.superuser = User.objects.create_superuser(
            username="transfer-admin",
            email="transfer-admin@example.com",
            password="pass1234",
        )
        self.source_house = PoultryHouse.objects.create(house_code="BROODER", name="Brooder", capacity=1000)
        self.house_a = PoultryHouse.objects.create(house_code="HOUSE-A", name="House A", capacity=500)
        self.house_b = PoultryHouse.objects.create(house_code="HOUSE-B", name="House B", capacity=500)
        self.supervisor.houses.add(self.source_house)
        self.batch = PoultryBatch.objects.create(
            batch_code="BROOD-001",
            house=self.source_house,
            breed="Layers",
            supplier_name="Hatchery",
            date_stocked=date.today(),
            initial_quantity=200,
            initial_age_days=42,
            status=PoultryBatch.Status.ACTIVE,
            created_by=self.manager,
        )

    def test_new_stocking_creates_house_ledger_balance(self):
        self.assertEqual(batch_bird_count(self.batch), 200)
        self.assertEqual(house_bird_count(self.source_house), 200)
        movement = HouseBirdMovement.objects.get(batch=self.batch)
        self.assertEqual(movement.movement_type, HouseBirdMovement.MovementType.STOCK_IN)
        self.assertEqual(movement.direction, HouseBirdMovement.Direction.IN)

    def test_supervisor_can_split_batch_across_multiple_active_houses(self):
        transfer = create_bird_transfer(
            actor=self.supervisor,
            source_batch_id=self.batch.pk,
            transfer_date=date.today(),
            allocations=[
                {"house_id": self.house_a.pk, "quantity": 100},
                {"house_id": self.house_b.pk, "quantity": 50},
            ],
            notes="Move growers out of brooder",
        )

        self.assertEqual(transfer.allocations.count(), 2)
        self.assertEqual(batch_bird_count(self.batch), 50)
        self.assertEqual(house_bird_count(self.source_house), 50)
        self.assertEqual(house_bird_count(self.house_a), 100)
        self.assertEqual(house_bird_count(self.house_b), 50)
        for allocation in transfer.allocations.select_related("destination_batch"):
            self.assertEqual(allocation.destination_batch.origin_batch, self.batch)
            self.assertEqual(batch_bird_count(allocation.destination_batch), allocation.quantity)

    def test_supervisor_transfer_page_is_available_but_manager_is_denied(self):
        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("bird_transfers"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Transfer birds between houses")
        self.assertContains(response, self.batch.batch_code)

        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("bird_transfers")).status_code, 403)

    def test_full_transfer_closes_source_batch(self):
        create_bird_transfer(
            actor=self.supervisor,
            source_batch_id=self.batch.pk,
            transfer_date=date.today(),
            allocations=[{"house_id": self.house_a.pk, "quantity": 200}],
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, PoultryBatch.Status.CLOSED)

    def test_capacity_warning_requires_acknowledgement(self):
        self.house_a.capacity = 50
        self.house_a.save(update_fields=["capacity"])
        with self.assertRaises(ValidationError):
            create_bird_transfer(
                actor=self.supervisor,
                source_batch_id=self.batch.pk,
                transfer_date=date.today(),
                allocations=[{"house_id": self.house_a.pk, "quantity": 100}],
            )
        transfer = create_bird_transfer(
            actor=self.supervisor,
            source_batch_id=self.batch.pk,
            transfer_date=date.today(),
            allocations=[{"house_id": self.house_a.pk, "quantity": 100}],
            capacity_warning_acknowledged=True,
        )
        self.assertTrue(transfer.allocations.get().exceeded_capacity)

    def test_supervisor_cannot_transfer_from_unassigned_source(self):
        self.supervisor.houses.clear()
        with self.assertRaises(PermissionDenied):
            create_bird_transfer(
                actor=self.supervisor,
                source_batch_id=self.batch.pk,
                transfer_date=date.today(),
                allocations=[{"house_id": self.house_a.pk, "quantity": 10}],
            )

    def test_approved_mortality_reduces_correct_house(self):
        cause = MortalityCause.objects.create(name="Natural")
        mortality = MortalityRecord.objects.create(
            batch=self.batch,
            record_date=date.today(),
            number_dead=7,
            cause=cause,
            status=ApprovalStatus.PENDING,
            reported_by=self.supervisor,
        )
        self.client.force_login(self.supervisor)
        response = self.client.post(
            reverse("sup_approve_mortality", args=[mortality.pk]),
            {"action": "approve", "review_notes": "Verified"},
        )
        self.assertRedirects(response, reverse("supapproval"))
        self.assertEqual(batch_bird_count(self.batch), 193)
        self.assertEqual(house_bird_count(self.source_house), 193)
        self.assertTrue(HouseBirdMovement.objects.filter(mortality_record=mortality).exists())

    def test_only_django_superuser_can_reverse_transfer(self):
        transfer = create_bird_transfer(
            actor=self.supervisor,
            source_batch_id=self.batch.pk,
            transfer_date=date.today(),
            allocations=[{"house_id": self.house_a.pk, "quantity": 40}],
        )
        with self.assertRaises(PermissionDenied):
            reverse_bird_transfer(actor=self.manager, transfer_id=transfer.pk, reason="Wrong house")

        reverse_bird_transfer(actor=self.superuser, transfer_id=transfer.pk, reason="Wrong house")
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, BirdTransfer.Status.REVERSED)
        self.assertEqual(house_bird_count(self.source_house), 200)
        self.assertEqual(house_bird_count(self.house_a), 0)

    def test_delivered_bird_sale_reduces_selected_house_and_batch(self):
        customer = Customer.objects.create(name="Ledger Bird Buyer")
        invoice = SaleInvoice.objects.create(
            invoice_no="INV-LEDGER-1",
            customer=customer,
            invoice_date=date.today(),
            total_amount=Decimal("50000.00"),
            status=SaleInvoice.Status.PAID,
            delivery_status=SaleInvoice.DeliveryStatus.DELIVERED,
            created_by=self.manager,
        )
        item = SaleItem.objects.create(
            invoice=invoice,
            batch=self.batch,
            product_name="Off Layer Birds",
            quantity=Decimal("5"),
            unit="birds",
            unit_price=Decimal("10000.00"),
            line_total=Decimal("50000.00"),
        )
        movement = record_delivered_bird_sale(item, operator=self.manager)
        self.assertEqual(movement.movement_type, HouseBirdMovement.MovementType.SALE)
        self.assertEqual(batch_bird_count(self.batch), 195)
        self.assertEqual(house_bird_count(self.source_house), 195)


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


class WorkerEggDisplayTests(TestCase):
    def setUp(self):
        worker_role = Role.objects.create(code=Role.RoleCode.WORKER, name="Worker")
        self.worker = User.objects.create_user(
            username="egg-worker",
            password="StrongPass1",
            role=worker_role,
        )
        self.house = PoultryHouse.objects.create(
            house_code="EGG-HSE-01",
            name="Egg House",
            capacity=500,
        )
        self.worker.houses.add(self.house)
        self.batch = PoultryBatch.objects.create(
            batch_code="EGG-BATCH-01",
            house=self.house,
            breed="Layers",
            supplier_name="Farm Source",
            date_stocked=date(2026, 1, 1),
            initial_quantity=300,
            initial_age_days=120,
            status=PoultryBatch.Status.ACTIVE,
            created_by=self.worker,
        )
        self.client.force_login(self.worker)

    def test_tray_formatter_preserves_loose_egg_balance(self):
        self.assertEqual(format_eggs_as_trays(302), "10 trays and 2 eggs")
        self.assertEqual(format_eggs_as_trays(30), "1 tray")
        self.assertEqual(format_eggs_as_trays(1), "1 egg")

    def test_worker_dashboard_total_includes_every_review_status(self):
        egg_collection.objects.create(
            batch=self.batch,
            collection_date=date.today(),
            eggs_collected=32,
            status=ApprovalStatus.PENDING,
            collected_by=self.worker,
        )
        egg_collection.objects.create(
            batch=self.batch,
            collection_date=date.today(),
            eggs_collected=30,
            status=ApprovalStatus.REJECTED,
            review_notes="Check the count and submit again.",
            collected_by=self.worker,
        )

        response = self.client.get(reverse("workersdash"))

        self.assertEqual(response.context["today_eggs"], 62)
        self.assertEqual(response.context["egg_approval_counts"]["pending"], 1)
        self.assertEqual(response.context["egg_approval_counts"]["rejected"], 1)
        self.assertContains(response, "1 tray and 2 eggs")
        self.assertContains(response, "1 pending")
        self.assertContains(response, "1 rejected")
        self.assertContains(response, "Needs correction")
        self.assertContains(response, "Awaiting review")

    def test_saved_collection_confirms_trays_and_loose_eggs(self):
        response = self.client.post(
            reverse("record_egg"),
            {
                "batch": str(self.batch.pk),
                "total_eggs": "302",
                "broken_eggs": "2",
                "notes": "Morning collection",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[-1][0], reverse("workersdash"))
        self.assertContains(response, "10 trays and 2 eggs")
        record = egg_collection.objects.get(
            batch=self.batch,
            eggs_collected=302,
            status=ApprovalStatus.PENDING,
        )
        self.assertIsNone(record.average_egg_weight_g)

    def test_egg_weight_submission_controls_are_disabled(self):
        response = self.client.get(reverse("record_egg"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="calculate_avg_weight"')
        self.assertNotContains(response, "Get Avg Weight")


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

    def test_approval_requires_post_and_keeps_pending_record_unchanged_on_get(self):
        self.client.force_login(self.supervisor)
        record = egg_collection.objects.get(batch=self.batch_a)

        response = self.client.get(reverse("sup_approve_eggs", args=[record.pk]))

        self.assertEqual(response.status_code, 405)
        record.refresh_from_db()
        self.assertEqual(record.status, ApprovalStatus.PENDING)
        self.assertIsNone(record.reviewed_by)

    def test_rejection_requires_worker_feedback(self):
        self.client.force_login(self.supervisor)
        record = egg_collection.objects.get(batch=self.batch_a)

        response = self.client.post(
            reverse("sup_approve_eggs", args=[record.pk]),
            {"action": "reject", "review_notes": ""},
        )

        self.assertRedirects(response, reverse("supapproval"))
        record.refresh_from_db()
        self.assertEqual(record.status, ApprovalStatus.PENDING)
        self.assertIsNone(record.reviewed_by)

    def test_pending_record_can_only_be_reviewed_once(self):
        self.client.force_login(self.supervisor)
        record = egg_collection.objects.get(batch=self.batch_a)
        url = reverse("sup_approve_eggs", args=[record.pk])

        first_response = self.client.post(url, {"action": "approve"})
        second_response = self.client.post(url, {"action": "approve"})

        self.assertRedirects(first_response, reverse("supapproval"))
        self.assertEqual(second_response.status_code, 404)


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


class InvestorAnalysisCatalogueTests(TestCase):
    def test_catalogue_and_guided_recipes_reference_valid_metadata(self):
        self.assertEqual(len(METRIC_CATALOG), 68)
        self.assertEqual(len(GUIDED_QUESTIONS), 6)
        self.assertEqual(
            set(TARGET_DIRECTIONS),
            {
                "profit_margin",
                "expense_ratio",
                "collection_rate",
                "cost_per_egg",
                "feed_cost_per_egg",
                "feed_per_egg",
                "revenue_per_egg",
                "egg_rejection_rate",
                "mortality_rate",
                "house_utilization",
            },
        )
        for question in GUIDED_QUESTIONS.values():
            self.assertTrue(question["metrics"])
            self.assertTrue(set(question["metrics"]).issubset(METRIC_CATALOG))
            for metric_key in question["metrics"]:
                self.assertIn(question["dimension"], METRIC_CATALOG[metric_key]["dimensions"])
        for key, metric in METRIC_CATALOG.items():
            self.assertEqual(metric["key"], key)
            self.assertTrue(metric["definition"])
            self.assertTrue(metric["source"])
            self.assertTrue(metric["dimensions"])

    def test_previous_period_is_inclusive_and_equal_length(self):
        current_start = date(2026, 2, 1)
        current_end = date(2026, 2, 28)
        previous_start, previous_end = previous_period(current_start, current_end)

        self.assertEqual(previous_start, date(2026, 1, 4))
        self.assertEqual(previous_end, date(2026, 1, 31))
        self.assertEqual(
            (current_end - current_start).days,
            (previous_end - previous_start).days,
        )


class InvestorKpiTargetTests(TestCase):
    def setUp(self):
        self.owner_role = Role.objects.create(code=Role.RoleCode.OWNER, name="Owner")
        self.manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Manager")
        self.owner = User.objects.create_user(username="target-owner", password="pass", role=self.owner_role)
        self.manager = User.objects.create_user(username="target-manager", password="pass", role=self.manager_role)

    def test_target_versions_close_intervals_without_overlap(self):
        create_target_version(
            metric_key="profit_margin",
            target_value=Decimal("20"),
            effective_from=date(2026, 1, 1),
            user=self.owner,
        )
        create_target_version(
            metric_key="profit_margin",
            target_value=Decimal("30"),
            effective_from=date(2026, 7, 1),
            user=self.owner,
        )
        create_target_version(
            metric_key="profit_margin",
            target_value=Decimal("25"),
            effective_from=date(2026, 4, 1),
            user=self.owner,
        )

        targets = list(InvestorKpiTarget.objects.filter(metric_key="profit_margin").order_by("effective_from"))
        self.assertEqual(
            [(target.effective_from, target.effective_to) for target in targets],
            [
                (date(2026, 1, 1), date(2026, 3, 31)),
                (date(2026, 4, 1), date(2026, 6, 30)),
                (date(2026, 7, 1), None),
            ],
        )
        self.assertEqual(
            active_targets(date(2026, 5, 1), ["profit_margin"])["profit_margin"].target_value,
            Decimal("25.0000"),
        )

    def test_only_owner_can_mutate_targets(self):
        payload = {
            "metricKey": "mortality_rate",
            "targetValue": "3.5",
            "effectiveFrom": "2026-01-01",
        }
        self.client.force_login(self.manager)
        denied = self.client.post(
            reverse("investor_targets"),
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(InvestorKpiTarget.objects.exists())

        self.client.force_login(self.owner)
        created = self.client.post(
            reverse("investor_targets"),
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        target = InvestorKpiTarget.objects.get()
        self.assertEqual(target.direction, InvestorKpiTarget.Direction.MAXIMUM)
        self.assertEqual(target.target_value, Decimal("3.5000"))

    def test_minimum_and_maximum_target_evaluation(self):
        minimum = create_target_version(
            metric_key="profit_margin",
            target_value=Decimal("20"),
            effective_from=date(2026, 1, 1),
            user=self.owner,
        )
        maximum = create_target_version(
            metric_key="mortality_rate",
            target_value=Decimal("4"),
            effective_from=date(2026, 1, 1),
            user=self.owner,
        )

        self.assertEqual(target_payload("profit_margin", Decimal("22"), minimum)["status"], "met")
        self.assertEqual(target_payload("profit_margin", Decimal("18"), minimum)["status"], "missed")
        self.assertEqual(target_payload("mortality_rate", Decimal("3"), maximum)["status"], "met")
        self.assertEqual(target_payload("mortality_rate", Decimal("5"), maximum)["status"], "missed")


class InvestorAnalysisEndpointTests(TestCase):
    def setUp(self):
        self.manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Manager")
        self.user = User.objects.create_user(
            username="analysis-manager",
            password="StrongPass1",
            role=self.manager_role,
        )
        self.house = PoultryHouse.objects.create(
            house_code="AN-HSE-01",
            name="Analysis House",
            capacity=500,
        )
        self.batch = PoultryBatch.objects.create(
            batch_code="AN-BATCH-01",
            house=self.house,
            breed="Layers",
            supplier_name="Supplier",
            amount_paid=Decimal("0"),
            date_stocked=date(2026, 1, 1),
            initial_quantity=200,
            initial_age_days=120,
            status=PoultryBatch.Status.ACTIVE,
            created_by=self.user,
        )
        self.customer = Customer.objects.create(name="Analysis Buyer")
        self.feed_category, _ = ExpenseCategory.objects.get_or_create(code="FEED", defaults={"name": "Feed"})

    def post_analysis(self, payload):
        return self.client.post(
            reverse("investor_analysis"),
            json.dumps(payload),
            content_type="application/json",
        )

    def test_endpoint_requires_authentication(self):
        response = self.post_analysis({
            "mode": "guided",
            "questionKey": "loss_drivers",
            "startDate": "2026-01-01",
            "endDate": "2026-01-31",
            "groupBy": "month",
            "scopeType": "farm",
            "dimension": "period",
            "view": "line",
        })
        self.assertEqual(response.status_code, 302)

    def test_guided_report_stays_within_query_budget_and_returns_null_ratios(self):
        self.client.force_login(self.user)
        with CaptureQueriesContext(connection) as queries:
            response = self.post_analysis({
                "mode": "guided",
                "questionKey": "high_costs",
                "startDate": "2026-01-01",
                "endDate": "2026-01-31",
                "groupBy": "month",
                "scopeType": "farm",
                "dimension": "period",
                "view": "line",
            })

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 60)
        payload = response.json()
        values = {metric["key"]: metric["value"] for metric in payload["metrics"]}
        self.assertIsNone(values["cost_per_egg"])
        self.assertIsNone(values["feed_cost_per_egg"])
        self.assertEqual(payload["meta"]["previousRange"], {"start": "2025-12-01", "end": "2025-12-31"})

    def test_batch_scope_prorates_invoice_cash_and_uses_allocated_expenses(self):
        invoice = SaleInvoice.objects.create(
            invoice_no="AN-INV-0001",
            customer=self.customer,
            invoice_date=date(2026, 1, 10),
            due_date=date(2026, 1, 20),
            subtotal=Decimal("1000"),
            total_amount=Decimal("1000"),
            status=SaleInvoice.Status.ISSUED,
            created_by=self.user,
        )
        SaleItem.objects.create(
            invoice=invoice,
            batch=self.batch,
            product_name="Eggs",
            quantity=Decimal("10"),
            unit="trays",
            unit_price=Decimal("75"),
            line_total=Decimal("750"),
        )
        SaleItem.objects.create(
            invoice=invoice,
            batch=None,
            product_name="Unallocated produce",
            quantity=Decimal("1"),
            unit="lot",
            unit_price=Decimal("250"),
            line_total=Decimal("250"),
        )
        CustomerPayment.objects.create(
            invoice=invoice,
            customer=self.customer,
            payment_date=date(2026, 1, 12),
            method=CustomerPayment.Method.CASH,
            amount=Decimal("800"),
            received_by=self.user,
        )
        ReceivableLedger.objects.create(
            invoice=invoice,
            amount_due=Decimal("1000"),
            amount_paid=Decimal("800"),
            balance=Decimal("200"),
        )
        expense = ExpenseTransaction.objects.create(
            expense_date=date(2026, 1, 11),
            category=self.feed_category,
            description="Scoped expense",
            total_amount=Decimal("500"),
            period_year=2026,
            period_month=1,
            status=ExpenseTransaction.Status.APPROVED,
            created_by=self.user,
        )
        ExpenseAllocation.objects.create(
            expense=expense,
            batch=self.batch,
            method=ExpenseAllocation.Method.DIRECT,
            amount_allocated=Decimal("300"),
        )
        InvestorKpiTarget.objects.create(
            metric_key="collection_rate",
            target_value=Decimal("80"),
            direction=InvestorKpiTarget.Direction.MINIMUM,
            effective_from=date(2026, 1, 1),
            created_by=self.user,
        )
        self.client.force_login(self.user)
        response = self.post_analysis({
            "mode": "analyst",
            "metricKeys": ["sales_revenue", "cash_received", "total_expenses", "collection_rate"],
            "startDate": "2026-01-01",
            "endDate": "2026-01-31",
            "groupBy": "month",
            "scopeType": "batch",
            "scopeId": self.batch.pk,
            "dimension": "period",
            "view": "line",
        })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        values = {metric["key"]: metric["value"] for metric in payload["metrics"]}
        self.assertEqual(values["sales_revenue"], 750.0)
        self.assertEqual(values["cash_received"], 600.0)
        self.assertEqual(values["total_expenses"], 300.0)
        self.assertEqual(values["collection_rate"], 80.0)
        collection_metric = next(metric for metric in payload["metrics"] if metric["key"] == "collection_rate")
        self.assertEqual(collection_metric["target"]["status"], "met")
        self.assertEqual(collection_metric["varianceState"], "unavailable")
        revenue_metric = next(metric for metric in payload["metrics"] if metric["key"] == "sales_revenue")
        self.assertEqual(revenue_metric["previousValue"], 0.0)
        self.assertIsNone(revenue_metric["percentChange"])
        self.assertEqual(revenue_metric["varianceState"], "favorable")
        self.assertEqual(payload["coverage"]["revenueAllocationPercent"], 75.0)
        self.assertEqual(payload["coverage"]["expenseAllocationPercent"], 60.0)

        house_response = self.post_analysis({
            "mode": "analyst",
            "metricKeys": ["sales_revenue", "total_expenses"],
            "startDate": "2026-01-01",
            "endDate": "2026-01-31",
            "groupBy": "month",
            "scopeType": "house",
            "scopeId": self.house.pk,
            "dimension": "period",
            "view": "pie",
        })
        self.assertEqual(house_response.status_code, 200)
        house_payload = house_response.json()
        house_values = {metric["key"]: metric["value"] for metric in house_payload["metrics"]}
        self.assertEqual(house_values["sales_revenue"], 750.0)
        self.assertEqual(house_values["total_expenses"], 300.0)
        self.assertEqual(house_payload["meta"]["view"], "line")
        self.assertTrue(house_payload["meta"]["viewAdjustment"])

    def test_invalid_analyst_dimension_and_metric_limit_are_rejected(self):
        self.client.force_login(self.user)
        invalid_dimension = self.post_analysis({
            "mode": "analyst",
            "metricKeys": ["sales_revenue", "total_expenses"],
            "startDate": "2026-01-01",
            "endDate": "2026-01-31",
            "scopeType": "farm",
            "groupBy": "month",
            "dimension": "customer",
            "view": "bar",
        })
        self.assertEqual(invalid_dimension.status_code, 400)

        too_many = self.post_analysis({
            "mode": "analyst",
            "metricKeys": list(METRIC_CATALOG)[:7],
            "startDate": "2026-01-01",
            "endDate": "2026-01-31",
            "scopeType": "farm",
            "groupBy": "month",
            "dimension": "period",
            "view": "bar",
        })
        self.assertEqual(too_many.status_code, 400)

    def test_investor_page_uses_new_builder_contract(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("investor"))

        self.assertContains(response, 'id="investorBuilderV2"')
        self.assertContains(response, 'id="investor-builder-catalog"')
        self.assertNotContains(response, 'id="investor-builder-data"')
        self.assertContains(response, 'id="analysisCsv"')
        self.assertContains(response, 'id="analysisPng"')
        self.assertContains(response, 'id="analysisPrint"')
        self.assertContains(response, "AI Advisor")


class FeedFormulaSeedAndScalingTests(TestCase):
    stages = [
        FlockStage.CHICK,
        FlockStage.GROWER,
        FlockStage.PRE_LAY,
        FlockStage.LAYER_1,
        FlockStage.LAYER_2,
    ]

    expected = {
        "Hendrix 5%": {
            "reference": Decimal("1000.00"),
            "ingredients": {
                "Layer Concentrate 5%": [60, 50, 50, 50, 50],
                "Stock Feed Lime": [20, 20, 90, 95, 100],
                "Maize": [180, 200, 180, 185, 175],
                "Maize bran": [520, 530, 490, 500, 525],
                "Soybean Meal": [120, 100, 100, 95, 90],
                "Sunflower Meal": [100, 100, 90, 75, 60],
            },
        },
        "Hendrix 10%": {
            "reference": Decimal("1000.00"),
            "ingredients": {
                "Maize bran": [500, 600, 500, 500, 500],
                "Broken Maize": [210, 170, 215, 210, 200],
                "Soybean Meal": [140, 25, 40, 105, 60],
                "Layer Concentrate 10%": [120, 100, 100, 100, 100],
                "Sunflower Meal": [20, 95, 100, 0, 50],
                "Stock Feed Lime": [10, 10, 45, 85, 90],
            },
        },
        "Hendrix 20%": {
            "reference": Decimal("250.00"),
            "ingredients": {
                "Layer Concentrate 20%": [50, 50, 50, 50, 50],
                "Stock Feed Lime": [0, 3, 20, 22, 24],
                "Maize": [50, 50, 43, 49, 48],
                "Maize bran": [125, 140, 130, 129, 128],
                "Sunflower Meal": [25, 7, 7, 0, 0],
            },
        },
    }

    def test_seeded_formula_matrix_matches_supplied_recipes(self):
        self.assertEqual(FeedFormulaTemplate.objects.filter(is_system=True).count(), 15)
        for formula_name, expected_formula in self.expected.items():
            for stage_index, stage in enumerate(self.stages):
                formula = FeedFormulaTemplate.objects.get(
                    name=formula_name,
                    flock_stage=stage,
                    is_system=True,
                )
                self.assertEqual(formula.reference_weight_kg, expected_formula["reference"])
                actual = {
                    line.item.name: line.quantity_kg
                    for line in formula.ingredients.select_related("item")
                }
                expected_lines = {
                    item_name: Decimal(str(quantities[stage_index]))
                    for item_name, quantities in expected_formula["ingredients"].items()
                    if quantities[stage_index] > 0
                }
                self.assertEqual(actual, expected_lines)
                self.assertEqual(sum(actual.values(), Decimal("0.00")), expected_formula["reference"])

    def test_stage_boundaries(self):
        expected = {
            0: FlockStage.CHICK,
            55: FlockStage.CHICK,
            56: FlockStage.GROWER,
            118: FlockStage.GROWER,
            119: FlockStage.PRE_LAY,
            139: FlockStage.PRE_LAY,
            140: FlockStage.LAYER_1,
            279: FlockStage.LAYER_1,
            280: FlockStage.LAYER_2,
            700: FlockStage.LAYER_2,
        }
        for age_days, stage in expected.items():
            with self.subTest(age_days=age_days):
                self.assertEqual(flock_stage_for_age_days(age_days), stage)

    def test_scaling_rounds_to_an_exact_target(self):
        formula = FeedFormulaTemplate.objects.get(name="Hendrix 5%", flock_stage=FlockStage.LAYER_1)
        lines = scale_formula_ingredients(formula, Decimal("123.47"))

        self.assertEqual(sum((line["quantity_kg"] for line in lines), Decimal("0.00")), Decimal("123.47"))
        self.assertTrue(all(line["quantity_kg"].as_tuple().exponent >= -2 for line in lines))


class FeedFormulaWorkflowTests(TestCase):
    def setUp(self):
        self.supervisor_role = Role.objects.create(code=Role.RoleCode.SUPERVISOR, name="Formula Supervisor")
        self.worker_role = Role.objects.create(code=Role.RoleCode.WORKER, name="Formula Worker")
        self.manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Formula Manager")
        self.supervisor = User.objects.create_user(
            username="formula-supervisor",
            password="pass1234",
            role=self.supervisor_role,
        )
        self.worker = User.objects.create_user(
            username="formula-worker",
            password="pass1234",
            role=self.worker_role,
        )
        self.manager = User.objects.create_user(
            username="formula-manager",
            password="pass1234",
            role=self.manager_role,
        )
        self.house = PoultryHouse.objects.create(
            house_code="FORMULA-H1",
            name="Formula House",
            capacity=1000,
        )
        self.other_house = PoultryHouse.objects.create(
            house_code="FORMULA-H2",
            name="Other House",
            capacity=1000,
        )
        self.supervisor.houses.add(self.house)
        self.worker.houses.add(self.house)
        self.batch_a = self._batch("FORMULA-A", self.house, 70)
        self.batch_b = self._batch("FORMULA-B", self.house, 80)
        self.chick_batch = self._batch("FORMULA-CHICK", self.house, 20)
        self.out_of_scope_batch = self._batch("FORMULA-OTHER", self.other_house, 70)
        self.formula = FeedFormulaTemplate.objects.get(
            name="Hendrix 5%",
            flock_stage=FlockStage.GROWER,
        )
        self.store = Store.objects.create(name="Formula Test Store")
        self._receive_formula_stock()

    def _batch(self, code, house, age_days):
        return PoultryBatch.objects.create(
            batch_code=code,
            house=house,
            breed="Layers",
            supplier_name="Formula Supplier",
            date_stocked=date.today(),
            initial_quantity=200,
            initial_age_days=age_days,
            status=PoultryBatch.Status.ACTIVE,
            created_by=self.manager,
        )

    def _receive_formula_stock(self):
        for item in Item.objects.filter(category__code="FEED"):
            InventoryTransaction.objects.create(
                tx_date=date.today(),
                tx_type=InventoryTransaction.TxType.IN_,
                store=self.store,
                item=item,
                quantity=Decimal("2000.000"),
                created_by=self.manager,
            )

    def _preset_post(self, **overrides):
        data = {
            "name": "Scaled Grower Mix",
            "mix_date": date.today().isoformat(),
            "primary_batch": str(self.batch_a.pk),
            "formula_template": str(self.formula.pk),
            "target_weight_kg": "100.00",
            "total_weight_kg": "99.00",
            "allocation_batch": [str(self.batch_a.pk), str(self.batch_b.pk)],
            "allocation_quantity": ["60.00", "39.00"],
            "notes": "Formula workflow test",
        }
        data.update(overrides)
        return data

    def test_supervisor_page_is_scoped_to_assigned_houses(self):
        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("feed_mixtures"))

        batch_ids = {batch.pk for batch in response.context["batches"]}
        self.assertIn(self.batch_a.pk, batch_ids)
        self.assertNotIn(self.out_of_scope_batch.pk, batch_ids)
        self.assertContains(response, "Hendrix 5%")

        self.client.force_login(self.manager)
        manager_response = self.client.get(reverse("feed_mixtures"))
        manager_batch_ids = {batch.pk for batch in manager_response.context["batches"]}
        self.assertIn(self.out_of_scope_batch.pk, manager_batch_ids)

    def test_preset_mix_scales_deducts_stock_and_allocates_by_batch(self):
        self.client.force_login(self.supervisor)
        response = self.client.post(reverse("feed_mixtures"), self._preset_post())

        self.assertRedirects(response, reverse("feed_mixtures"))
        mixture = FeedMixture.objects.get(name="Scaled Grower Mix")
        self.assertEqual(mixture.formula_template, self.formula)
        self.assertEqual(mixture.formula_name_snapshot, "Hendrix 5%")
        self.assertEqual(mixture.flock_stage, FlockStage.GROWER)
        self.assertEqual(mixture.planned_weight_kg, Decimal("100.00"))
        self.assertEqual(mixture.total_weight_kg, Decimal("99.00"))
        self.assertEqual(mixture.total_ingredient_kg, Decimal("100.00"))
        self.assertEqual(
            set(mixture.allocations.values_list("batch_id", flat=True)),
            {self.batch_a.pk, self.batch_b.pk},
        )
        stock_out = InventoryTransaction.objects.filter(reference=f"MIX-{mixture.pk}")
        self.assertEqual(stock_out.count(), mixture.ingredients.count())
        self.assertEqual(
            stock_out.aggregate(total=Sum("quantity"))["total"],
            Decimal("100.000"),
        )

    def test_server_rejects_short_stock_without_partial_writes(self):
        self.client.force_login(self.supervisor)
        response = self.client.post(
            reverse("feed_mixtures"),
            self._preset_post(
                target_weight_kg="10000.00",
                total_weight_kg="10000.00",
                allocation_quantity=["6000.00", "4000.00"],
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(FeedMixture.objects.filter(name="Scaled Grower Mix").exists())
        self.assertFalse(InventoryTransaction.objects.filter(reference__startswith="MIX-").exists())
        self.assertContains(response, "is available")

    def test_cross_stage_allocation_is_rejected(self):
        self.client.force_login(self.supervisor)
        response = self.client.post(
            reverse("feed_mixtures"),
            self._preset_post(
                allocation_batch=[str(self.batch_a.pk), str(self.chick_batch.pk)],
                allocation_quantity=["50.00", "49.00"],
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(FeedMixture.objects.filter(name="Scaled Grower Mix").exists())
        self.assertContains(response, "not in the same feed stage")

    def test_custom_formula_can_be_saved_reused_and_archived(self):
        maize = Item.objects.get(name="Maize")
        maize_bran = Item.objects.get(name__iexact="Maize bran")
        self.client.force_login(self.supervisor)
        response = self.client.post(
            reverse("feed_mixtures"),
            {
                "name": "Farm Custom Mix",
                "mix_date": date.today().isoformat(),
                "primary_batch": str(self.batch_a.pk),
                "formula_template": "custom",
                "target_weight_kg": "100.00",
                "total_weight_kg": "100.00",
                "ingredient_item": [str(maize.pk), str(maize_bran.pk)],
                "ingredient_quantity": ["40.00", "60.00"],
                "allocation_batch": [str(self.batch_a.pk)],
                "allocation_quantity": ["100.00"],
                "save_custom": "on",
                "custom_formula_name": "Farm Grower Formula",
            },
        )

        self.assertRedirects(response, reverse("feed_mixtures"))
        custom = FeedFormulaTemplate.objects.get(name="Farm Grower Formula", is_active=True)
        self.assertFalse(custom.is_system)
        self.assertEqual(custom.flock_stage, FlockStage.GROWER)
        self.assertEqual(custom.reference_weight_kg, Decimal("100.00"))
        self.assertEqual(custom.ingredients.count(), 2)
        mixture = FeedMixture.objects.get(name="Farm Custom Mix")
        self.assertEqual(mixture.formula_template, custom)

        reused = self.client.post(
            reverse("feed_mixtures"),
            {
                "name": "Reused Farm Formula",
                "mix_date": date.today().isoformat(),
                "primary_batch": str(self.batch_a.pk),
                "formula_template": str(custom.pk),
                "target_weight_kg": "50.00",
                "total_weight_kg": "50.00",
                "allocation_batch": [str(self.batch_a.pk)],
                "allocation_quantity": ["50.00"],
            },
        )
        self.assertRedirects(reused, reverse("feed_mixtures"))
        reused_mixture = FeedMixture.objects.get(name="Reused Farm Formula")
        self.assertEqual(reused_mixture.formula_template, custom)
        self.assertEqual(
            {
                line.item.name: line.quantity_kg
                for line in reused_mixture.ingredients.select_related("item")
            },
            {"Maize": Decimal("20.00"), "Maize bran": Decimal("30.00")},
        )

        archive_response = self.client.post(reverse("archive_feed_formula", args=[custom.pk]))
        self.assertRedirects(archive_response, reverse("feed_mixtures"))
        custom.refresh_from_db()
        self.assertFalse(custom.is_active)
        mixture.refresh_from_db()
        self.assertEqual(mixture.formula_template, custom)

    def test_worker_cannot_record_a_batch_specific_mix_against_another_batch(self):
        self.client.force_login(self.supervisor)
        data = self._preset_post(
            total_weight_kg="100.00",
            allocation_batch=[str(self.batch_a.pk)],
            allocation_quantity=["100.00"],
        )
        self.client.post(reverse("feed_mixtures"), data)
        mixture = FeedMixture.objects.get(name="Scaled Grower Mix")

        self.client.force_login(self.worker)
        rejected = self.client.post(
            reverse("record_feed"),
            {
                "batch": str(self.batch_b.pk),
                "feed_mixture": str(mixture.pk),
                "quantity": "10.00",
            },
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertFalse(FeedRecord.objects.filter(feed_mixture=mixture).exists())

        accepted = self.client.post(
            reverse("record_feed"),
            {
                "batch": str(self.batch_a.pk),
                "feed_mixture": str(mixture.pk),
                "quantity": "10.00",
            },
        )
        self.assertRedirects(accepted, reverse("record_feed"))
        self.assertTrue(FeedRecord.objects.filter(feed_mixture=mixture, batch=self.batch_a).exists())

    def test_legacy_house_allocation_remains_available(self):
        mixture = FeedMixture.objects.create(
            name="Legacy House Mix",
            mix_date=date.today(),
            total_weight_kg=Decimal("20.00"),
            mixed_by=self.supervisor,
        )
        FeedMixtureAllocation.objects.create(
            mixture=mixture,
            house=self.house,
            quantity_kg=Decimal("20.00"),
        )
        self.client.force_login(self.worker)

        response = self.client.post(
            reverse("record_feed"),
            {
                "batch": str(self.batch_b.pk),
                "feed_mixture": str(mixture.pk),
                "quantity": "5.00",
            },
        )

        self.assertRedirects(response, reverse("record_feed"))
        self.assertTrue(FeedRecord.objects.filter(feed_mixture=mixture, batch=self.batch_b).exists())
