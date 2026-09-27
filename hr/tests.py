from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from payroll.models import SalaryPayment
from .models import (
    ContractRenewal,
    SalaryAdvanceAllocation,
    StaffProfile,
    UnpaidAbsence,
    WelfareRequest,
)


class StaffProfileTests(TestCase):
    def setUp(self):
        self.worker_role = Role.objects.create(code=Role.RoleCode.WORKER, name="Farm Worker")
        self.manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Farm Manager")
        self.owner_role = Role.objects.create(code=Role.RoleCode.OWNER, name="Business Owner")
        self.staff_role = Role.objects.create(code="STAFF", name="General Staff")
        self.worker = User.objects.create_user(username="worker-profile", password="Pass1234!", role=self.worker_role)
        self.manager = User.objects.create_user(username="manager-profile", password="Pass1234!", role=self.manager_role)
        self.owner = User.objects.create_user(username="owner-profile", password="Pass1234!", role=self.owner_role)

    def test_staff_user_can_view_own_profile(self):
        self.client.force_login(self.worker)

        response = self.client.get(reverse("staff_profile_detail", args=[self.worker.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "staff_profile_detail.html")
        self.assertContains(response, "worker-profile")
        profile = StaffProfile.objects.get(user=self.worker)
        self.assertRegex(profile.employee_number, r"^EMP-\d{4}-\d{5}$")

    def test_staff_user_cannot_view_another_staff_profile(self):
        other = User.objects.create_user(username="other-worker", password="Pass1234!", role=self.worker_role)
        self.client.force_login(self.worker)

        response = self.client.get(reverse("staff_profile_detail", args=[other.pk]))

        self.assertRedirects(response, reverse("staff_profile_detail", args=[self.worker.pk]))

    def test_manager_can_edit_dob_and_hourly_profile_but_not_system_employee_id(self):
        profile = StaffProfile.objects.create(user=self.worker)
        original_id = profile.employee_number
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("edit_staff_profile", args=[self.worker.pk]),
            {
                "full_name": "Grace Auma",
                "phone_number": "0700000000",
                "email": "grace@example.com",
                "date_of_birth": "1997-04-12",
                "employee_number": "SHOULD-NOT-BE-USED",
                "job_title": "Farm Attendant",
                "tin_number": "1000000000",
                "nssf_number": "NSSF-001",
                "national_id": "CM0001",
                "next_of_kin_name": "Auma Kin",
                "next_of_kin_contact": "0711111111",
                "physical_address": "Kampala",
                "emergency_contact": "0722222222",
                "monthly_salary": "0",
                "pay_basis": StaffProfile.PayBasis.HOURLY,
                "hourly_rate": "5500",
                "pay_nssf": "on",
                "pay_paye": "on",
                "pay_lst": "on",
                "lst_local_government": "Kira Municipal Council",
                "employment_status": StaffProfile.EmploymentStatus.ACTIVE,
                "hire_date": "2026-01-01",
                "contract_start_date": "2026-01-01",
                "contract_end_date": "2026-12-31",
            },
        )

        self.assertRedirects(response, reverse("staff_profile_detail", args=[self.worker.pk]))
        self.worker.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(self.worker.display_name, "Grace Auma")
        self.assertEqual(profile.date_of_birth.isoformat(), "1997-04-12")
        self.assertEqual(profile.employee_number, original_id)
        self.assertEqual(profile.pay_basis, StaffProfile.PayBasis.HOURLY)
        self.assertEqual(profile.hourly_rate, Decimal("5500.00"))
        self.assertTrue(profile.pay_lst)
        self.assertEqual(profile.lst_local_government, "Kira Municipal Council")

    def test_add_user_issues_employee_id_and_saves_onboarding_pay_basis(self):
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
                "role": str(self.staff_role.pk),
                "is_active": "True",
                "date_of_birth": "1995-06-15",
                "employee_number": "MANUAL-ID-IGNORED",
                "job_title": "Stores Clerk",
                "hire_date": "2026-06-01",
                "monthly_salary": "850000",
                "pay_basis": StaffProfile.PayBasis.MONTHLY,
                "hourly_rate": "0",
                "pay_nssf": "on",
                "pay_paye": "on",
                "pay_lst": "on",
                "lst_local_government": "Kampala Capital City Authority",
                "employment_status": StaffProfile.EmploymentStatus.ACTIVE,
                "notes": "Works in stores",
            },
        )

        self.assertRedirects(response, reverse("manage_users"))
        profile = StaffProfile.objects.get(user__username="new-staff")
        self.assertRegex(profile.employee_number, r"^EMP-\d{4}-\d{5}$")
        self.assertNotEqual(profile.employee_number, "MANUAL-ID-IGNORED")
        self.assertEqual(profile.date_of_birth.isoformat(), "1995-06-15")
        self.assertEqual(profile.monthly_salary, Decimal("850000.00"))
        self.assertTrue(profile.pay_nssf)
        self.assertTrue(profile.pay_paye)
        self.assertTrue(profile.pay_lst)

    def test_manager_can_record_unpaid_absence_and_renew_contract(self):
        profile = StaffProfile.objects.create(user=self.worker, contract_end_date=date(2026, 1, 31))
        self.client.force_login(self.manager)

        absence_response = self.client.post(
            reverse("record_unpaid_absence", args=[self.worker.pk]),
            {"absence_date": timezone.localdate().isoformat(), "reason": "Unauthorised absence"},
        )
        renewal_response = self.client.post(
            reverse("renew_staff_contract", args=[self.worker.pk]),
            {
                "renewed_contract_start_date": "2026-02-01",
                "renewed_contract_end_date": "2027-01-31",
                "renewal_notes": "Renewed for one year",
            },
        )

        self.assertRedirects(absence_response, reverse("staff_profile_detail", args=[self.worker.pk]))
        self.assertRedirects(renewal_response, reverse("staff_profile_detail", args=[self.worker.pk]))
        self.assertTrue(UnpaidAbsence.objects.filter(staff_profile=profile, reason="Unauthorised absence").exists())
        renewal = ContractRenewal.objects.get(staff_profile=profile)
        self.assertEqual(renewal.previous_contract_end_date, date(2026, 1, 31))
        profile.refresh_from_db()
        self.assertEqual(profile.contract_end_date, date(2027, 1, 31))

    @patch("hr.views.post_salary_advance")
    def test_manager_issues_approved_advance_with_audit_fields_and_register_history(self, post_advance):
        post_advance.return_value = SimpleNamespace(reference="ADV-42")
        advance = WelfareRequest.objects.create(
            worker=self.worker,
            request_type=WelfareRequest.RequestType.SALARY_ADVANCE,
            title="School fees",
            details="Advance for school fees",
            advance_amount=Decimal("300000.00"),
            advance_period_start=date(2026, 9, 1),
            advance_period_end=date(2026, 10, 31),
            status=WelfareRequest.Status.MANAGER_APPROVED,
            manager=self.manager,
        )
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("disburse_salary_advance", args=[advance.pk]),
            {
                "advance_disbursed_on": timezone.localdate().isoformat(),
                "advance_payment_method": WelfareRequest.AdvancePaymentMethod.MOBILE_MONEY,
                "advance_payment_reference": "MM-REF-123",
            },
        )

        self.assertRedirects(response, reverse("advance_register"))
        advance.refresh_from_db()
        self.assertEqual(advance.advance_payment_method, WelfareRequest.AdvancePaymentMethod.MOBILE_MONEY)
        self.assertEqual(advance.advance_payment_reference, "MM-REF-123")
        self.assertEqual(advance.advance_journal_reference, "ADV-42")
        self.assertEqual(advance.advance_disbursed_by, self.manager)
        post_advance.assert_called_once()
        register_response = self.client.get(reverse("advance_register"))
        self.assertContains(register_response, "MM-REF-123")
        self.assertContains(register_response, "Sep 2026")

    def test_issued_advance_can_be_recovered_across_multiple_salary_months(self):
        advance = WelfareRequest.objects.create(
            worker=self.worker,
            request_type=WelfareRequest.RequestType.SALARY_ADVANCE,
            title="Emergency expense",
            details="Emergency expense",
            advance_amount=Decimal("300000.00"),
            advance_period_start=date(2026, 9, 1),
            advance_period_end=date(2026, 10, 31),
            status=WelfareRequest.Status.MANAGER_APPROVED,
            manager=self.manager,
            advance_disbursed_on=date(2026, 9, 10),
            advance_payment_method=WelfareRequest.AdvancePaymentMethod.CASH,
            advance_payment_reference="CASH-101",
            advance_disbursed_by=self.manager,
        )
        september = SalaryPayment.objects.create(
            employee=self.worker,
            period_month="2026-09",
            amount=Decimal("200000.00"),
            net_pay=Decimal("200000.00"),
        )
        october = SalaryPayment.objects.create(
            employee=self.worker,
            period_month="2026-10",
            amount=Decimal("200000.00"),
            net_pay=Decimal("200000.00"),
        )
        SalaryAdvanceAllocation.objects.create(advance=advance, salary_payment=september, amount=Decimal("150000.00"))
        SalaryAdvanceAllocation.objects.create(advance=advance, salary_payment=october, amount=Decimal("150000.00"))

        advance.refresh_from_db()
        self.assertEqual(advance.advance_allocated_amount, Decimal("300000.00"))
        self.assertEqual(advance.advance_outstanding_amount, Decimal("0.00"))

    def test_investor_excluded_from_staff_profiles(self):
        self.client.force_login(self.manager)

        response = self.client.get(reverse("staff_profiles"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "worker-profile")
        self.assertNotContains(response, "owner-profile")
