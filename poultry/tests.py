from django.test import TestCase
from django.urls import reverse
from datetime import date

from accounts.models import Role, User

from .models import ApprovalStatus, PoultryBatch, PoultryHouse, egg_collection


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
