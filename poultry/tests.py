from django.test import TestCase
from django.urls import reverse

from accounts.models import User

from .models import PoultryBatch, PoultryHouse


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
