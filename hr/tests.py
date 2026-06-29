from django.test import TestCase
from django.urls import reverse
from decimal import Decimal

from accounts.models import Role, User
from .models import StaffProfile


class StaffProfileTests(TestCase):
    def setUp(self):
        self.worker_role = Role.objects.create(code=Role.RoleCode.WORKER, name="Farm Worker")
        self.manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Farm Manager")
        self.owner_role = Role.objects.create(code=Role.RoleCode.OWNER, name="Business Owner")
        self.worker = User.objects.create_user(username="worker-profile", password="Pass1234!", role=self.worker_role)
        self.manager = User.objects.create_user(username="manager-profile", password="Pass1234!", role=self.manager_role)
        self.owner = User.objects.create_user(username="owner-profile", password="Pass1234!", role=self.owner_role)

    def test_staff_user_can_view_own_profile(self):
        self.client.force_login(self.worker)

        response = self.client.get(reverse("staff_profile_detail", args=[self.worker.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "staff_profile_detail.html")
        self.assertContains(response, "worker-profile")
        self.assertTrue(StaffProfile.objects.filter(user=self.worker).exists())

    def test_staff_user_cannot_view_another_staff_profile(self):
        other = User.objects.create_user(username="other-worker", password="Pass1234!", role=self.worker_role)
        self.client.force_login(self.worker)

        response = self.client.get(reverse("staff_profile_detail", args=[other.pk]))

        self.assertRedirects(response, reverse("staff_profile_detail", args=[self.worker.pk]))

    def test_manager_can_edit_staff_profile(self):
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("edit_staff_profile", args=[self.worker.pk]),
            {
                "full_name": "Grace Auma",
                "phone_number": "0700000000",
                "email": "grace@example.com",
                "age": "29",
                "employee_number": "EMP-001",
                "job_title": "Farm Attendant",
                "tin_number": "1000000000",
                "nssf_number": "NSSF-001",
                "national_id": "CM0001",
                "next_of_kin_name": "Auma Kin",
                "next_of_kin_contact": "0711111111",
                "physical_address": "Kampala",
                "emergency_contact": "0722222222",
                "monthly_salary": "500000",
                "employment_status": StaffProfile.EmploymentStatus.ACTIVE,
            },
        )

        self.assertRedirects(response, reverse("staff_profile_detail", args=[self.worker.pk]))
        self.worker.refresh_from_db()
        profile = self.worker.staff_profile
        self.assertEqual(self.worker.display_name, "Grace Auma")
        self.assertEqual(profile.monthly_salary, Decimal("500000.00"))
        self.assertEqual(profile.nssf_number, "NSSF-001")
        self.assertFalse(profile.pay_nssf)
        self.assertFalse(profile.pay_paye)

    def test_add_user_saves_gross_salary_and_payroll_deductions(self):
        staff_role = Role.objects.create(code="STAFF", name="General Staff")
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("add_user"),
            {
                "username": "new-staff",
                "full_name": "New Staff",
                "email": "new@example.com",
                "phone_number": "0700000001",
                "password": "Pass1234!",
                "confirm_password": "Pass1234!",
                "role": str(staff_role.pk),
                "is_active": "True",
                "age": "31",
                "employee_number": "EMP-009",
                "job_title": "Stores Clerk",
                "tin_number": "TIN-009",
                "nssf_number": "NSSF-009",
                "national_id": "NIN-009",
                "hire_date": "2026-06-01",
                "next_of_kin_name": "Kin Person",
                "next_of_kin_contact": "0700000099",
                "physical_address": "Mukono",
                "emergency_contact": "0710000099",
                "monthly_salary": "850000",
                "pay_paye": "on",
                "employment_status": StaffProfile.EmploymentStatus.ACTIVE,
                "notes": "Works in stores",
            },
        )

        self.assertRedirects(response, reverse("manage_users"))
        profile = StaffProfile.objects.get(user__username="new-staff")
        self.assertEqual(profile.age, 31)
        self.assertEqual(profile.employee_number, "EMP-009")
        self.assertEqual(profile.job_title, "Stores Clerk")
        self.assertEqual(profile.tin_number, "TIN-009")
        self.assertEqual(profile.nssf_number, "NSSF-009")
        self.assertEqual(profile.national_id, "NIN-009")
        self.assertEqual(profile.hire_date.isoformat(), "2026-06-01")
        self.assertEqual(profile.next_of_kin_name, "Kin Person")
        self.assertEqual(profile.next_of_kin_contact, "0700000099")
        self.assertEqual(profile.physical_address, "Mukono")
        self.assertEqual(profile.emergency_contact, "0710000099")
        self.assertEqual(profile.monthly_salary, Decimal("850000.00"))
        self.assertFalse(profile.pay_nssf)
        self.assertTrue(profile.pay_paye)
        self.assertEqual(profile.notes, "Works in stores")

    def test_investor_excluded_from_staff_profiles(self):
        self.client.force_login(self.manager)

        response = self.client.get(reverse("staff_profiles"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "worker-profile")
        self.assertNotContains(response, "owner-profile")
