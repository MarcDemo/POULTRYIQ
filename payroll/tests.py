from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from decimal import Decimal

from accounts.models import Role
from hr.models import StaffProfile, WelfareRequest
from .models import SalaryBonus, SalaryPayment, SalaryPaymentEditLog


class SalariesViewTests(TestCase):
    def test_salaries_page_renders(self):
        role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Farm Manager")
        user = get_user_model().objects.create_user(
            username="salary-user",
            password="pass1234",
            role=role,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("salaries"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "payroll/salaries.html")
        self.assertContains(response, "Salary Management")

    def test_generate_monthly_payroll_deducts_approved_advances(self):
        manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Farm Manager")
        worker_role = Role.objects.create(code=Role.RoleCode.WORKER, name="Farm Worker")
        manager = get_user_model().objects.create_user(
            username="payroll-manager",
            password="pass1234",
            role=manager_role,
        )
        worker = get_user_model().objects.create_user(
            username="worker-payroll",
            password="pass1234",
            role=worker_role,
        )
        StaffProfile.objects.create(user=worker, monthly_salary="1000000.00")
        WelfareRequest.objects.create(
            worker=worker,
            request_type=WelfareRequest.RequestType.SALARY_ADVANCE,
            title="School fees",
            details="Advance request",
            advance_amount="100000.00",
            status=WelfareRequest.Status.MANAGER_APPROVED,
            manager=manager,
            manager_reviewed_at=timezone.now(),
        )
        self.client.force_login(manager)

        response = self.client.post(
            reverse("salaries"),
            {"action": "generate_monthly", "batch_month": "2026-06"},
        )

        self.assertRedirects(response, reverse("salaries"))
        salary = SalaryPayment.objects.get(employee=worker, period_month="2026-06")
        self.assertEqual(salary.gross_salary, Decimal("1000000.00"))
        self.assertEqual(salary.advances_deducted, Decimal("100000.00"))
        self.assertEqual(salary.payment_date.isoformat(), "2026-06-28")
        self.assertEqual(WelfareRequest.objects.get(worker=worker).salary_payment, salary)

    def test_generate_monthly_payroll_includes_managers_and_bonuses(self):
        manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Farm Manager")
        manager = get_user_model().objects.create_user(
            username="bonus-payroll-manager",
            password="pass1234",
            role=manager_role,
        )
        StaffProfile.objects.create(user=manager, monthly_salary="1000000.00", pay_nssf=False, pay_paye=False)
        SalaryBonus.objects.create(
            employee=manager,
            period_month="2026-06",
            amount=Decimal("150000.00"),
            reason="Performance",
            granted_by=manager,
        )
        self.client.force_login(manager)

        response = self.client.post(
            reverse("salaries"),
            {"action": "generate_monthly", "batch_month": "2026-06"},
        )

        self.assertRedirects(response, reverse("salaries"))
        salary = SalaryPayment.objects.get(employee=manager, period_month="2026-06")
        self.assertEqual(salary.bonus_amount, Decimal("150000.00"))
        self.assertEqual(salary.net_pay, Decimal("1150000.00"))

    def test_generate_monthly_payroll_creates_editable_rows_without_profile_salary(self):
        manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Farm Manager")
        worker_role = Role.objects.create(code=Role.RoleCode.WORKER, name="Farm Worker")
        manager = get_user_model().objects.create_user(
            username="zero-payroll-manager",
            password="pass1234",
            role=manager_role,
        )
        worker = get_user_model().objects.create_user(
            username="zero-salary-worker",
            password="pass1234",
            role=worker_role,
        )
        self.client.force_login(manager)

        response = self.client.post(
            reverse("salaries"),
            {"action": "generate_monthly", "batch_month": "2026-06"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        salary = SalaryPayment.objects.get(employee=worker, period_month="2026-06")
        self.assertEqual(salary.gross_salary, Decimal("0.00"))
        self.assertContains(response, "zero-salary-worker")
        self.assertContains(response, reverse("edit_salary", args=[salary.pk]))

    def test_generate_monthly_payroll_respects_staff_tax_choices(self):
        manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Farm Manager")
        worker_role = Role.objects.create(code=Role.RoleCode.WORKER, name="Farm Worker")
        manager = get_user_model().objects.create_user(
            username="tax-choice-manager",
            password="pass1234",
            role=manager_role,
        )
        worker = get_user_model().objects.create_user(
            username="tax-choice-worker",
            password="pass1234",
            role=worker_role,
        )
        StaffProfile.objects.create(
            user=worker,
            monthly_salary="1000000.00",
            pay_nssf=False,
            pay_paye=False,
        )
        self.client.force_login(manager)

        response = self.client.post(
            reverse("salaries"),
            {"action": "generate_monthly", "batch_month": "2026-06"},
        )

        self.assertRedirects(response, reverse("salaries"))
        salary = SalaryPayment.objects.get(employee=worker, period_month="2026-06")
        self.assertEqual(salary.gross_salary, Decimal("1000000.00"))
        self.assertEqual(salary.nssf_employee, Decimal("0.00"))
        self.assertEqual(salary.nssf_employer, Decimal("0.00"))
        self.assertEqual(salary.paye_tax, Decimal("0.00"))
        self.assertEqual(salary.net_pay, Decimal("1000000.00"))

    def test_manager_can_edit_prepared_salary_record(self):
        manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Farm Manager")
        worker_role = Role.objects.create(code=Role.RoleCode.WORKER, name="Farm Worker")
        manager = get_user_model().objects.create_user(
            username="salary-editor",
            password="pass1234",
            role=manager_role,
        )
        worker = get_user_model().objects.create_user(
            username="salary-edit-worker",
            password="pass1234",
            role=worker_role,
        )
        salary = SalaryPayment.objects.create(
            employee=worker,
            period_month="2026-06",
            amount=Decimal("500000.00"),
            gross_salary=Decimal("600000.00"),
            paye_tax=Decimal("10000.00"),
            nssf_employee=Decimal("30000.00"),
            nssf_employer=Decimal("60000.00"),
            advances_deducted=Decimal("60000.00"),
            net_pay=Decimal("500000.00"),
            payment_date="2026-06-28",
            status=SalaryPayment.Status.PENDING,
            recorded_by=manager,
        )
        self.client.force_login(manager)

        response = self.client.get(reverse("salaries"))
        self.assertContains(response, reverse("edit_salary", args=[salary.pk]))

        response = self.client.post(
            reverse("edit_salary", args=[salary.pk]),
            {
                "gross_salary": "700000",
                "advances_deducted": "50000",
                "bonus_amount": "10000",
                "net_pay": "595000",
                "payment_date": "2026-06-28",
                "status": SalaryPayment.Status.PAID,
                "calculate_from": "gross",
                "edit_reason": "Corrected monthly salary",
            },
        )

        self.assertRedirects(response, reverse("salaries"))
        salary.refresh_from_db()
        self.assertEqual(salary.gross_salary, Decimal("700000.00"))
        self.assertEqual(salary.nssf_employee, Decimal("35000.00"))
        self.assertEqual(salary.net_pay, Decimal("523500.00"))
        self.assertEqual(salary.amount, Decimal("523500.00"))
        self.assertEqual(salary.status, SalaryPayment.Status.PAID)
        self.assertEqual(salary.edit_reason, "Corrected monthly salary")
        self.assertTrue(SalaryPaymentEditLog.objects.filter(salary=salary, reason="Corrected monthly salary").exists())

    def test_salary_edit_requires_reason(self):
        manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Farm Manager")
        worker_role = Role.objects.create(code=Role.RoleCode.WORKER, name="Farm Worker")
        manager = get_user_model().objects.create_user(
            username="salary-reason-manager",
            password="pass1234",
            role=manager_role,
        )
        worker = get_user_model().objects.create_user(
            username="salary-reason-worker",
            password="pass1234",
            role=worker_role,
        )
        salary = SalaryPayment.objects.create(
            employee=worker,
            period_month="2026-06",
            amount=Decimal("500000.00"),
            gross_salary=Decimal("500000.00"),
            net_pay=Decimal("500000.00"),
            status=SalaryPayment.Status.PENDING,
            recorded_by=manager,
        )
        self.client.force_login(manager)

        response = self.client.post(
            reverse("edit_salary", args=[salary.pk]),
            {
                "gross_salary": "700000",
                "advances_deducted": "0",
                "bonus_amount": "0",
                "net_pay": "700000",
                "status": SalaryPayment.Status.PENDING,
                "calculate_from": "gross",
            },
        )

        self.assertEqual(response.status_code, 200)
        salary.refresh_from_db()
        self.assertEqual(salary.gross_salary, Decimal("500000.00"))

    def test_salary_edit_can_calculate_from_net_pay(self):
        manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Farm Manager")
        worker_role = Role.objects.create(code=Role.RoleCode.WORKER, name="Farm Worker")
        manager = get_user_model().objects.create_user(
            username="salary-net-manager",
            password="pass1234",
            role=manager_role,
        )
        worker = get_user_model().objects.create_user(
            username="salary-net-worker",
            password="pass1234",
            role=worker_role,
        )
        StaffProfile.objects.create(user=worker, pay_nssf=False, pay_paye=False)
        salary = SalaryPayment.objects.create(
            employee=worker,
            period_month="2026-06",
            amount=Decimal("500000.00"),
            gross_salary=Decimal("500000.00"),
            net_pay=Decimal("500000.00"),
            status=SalaryPayment.Status.PENDING,
            recorded_by=manager,
        )
        self.client.force_login(manager)

        response = self.client.post(
            reverse("edit_salary", args=[salary.pk]),
            {
                "gross_salary": "500000",
                "advances_deducted": "50000",
                "bonus_amount": "10000",
                "net_pay": "800000",
                "status": SalaryPayment.Status.PENDING,
                "calculate_from": "net",
                "edit_reason": "Set agreed net pay",
            },
        )

        self.assertRedirects(response, reverse("salaries"))
        salary.refresh_from_db()
        self.assertEqual(salary.gross_salary, Decimal("840000.00"))
        self.assertEqual(salary.net_pay, Decimal("800000.00"))

    def test_manager_can_pay_selected_salary_records(self):
        manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Farm Manager")
        worker_role = Role.objects.create(code=Role.RoleCode.WORKER, name="Farm Worker")
        manager = get_user_model().objects.create_user(
            username="selected-pay-manager",
            password="pass1234",
            role=manager_role,
        )
        first_worker = get_user_model().objects.create_user(
            username="selected-pay-one",
            password="pass1234",
            role=worker_role,
        )
        second_worker = get_user_model().objects.create_user(
            username="selected-pay-two",
            password="pass1234",
            role=worker_role,
        )
        first_salary = SalaryPayment.objects.create(
            employee=first_worker,
            period_month="2026-06",
            amount=Decimal("500000.00"),
            net_pay=Decimal("500000.00"),
            status=SalaryPayment.Status.PENDING,
            recorded_by=manager,
        )
        second_salary = SalaryPayment.objects.create(
            employee=second_worker,
            period_month="2026-06",
            amount=Decimal("600000.00"),
            net_pay=Decimal("600000.00"),
            status=SalaryPayment.Status.PENDING,
            recorded_by=manager,
        )
        self.client.force_login(manager)

        response = self.client.post(
            reverse("salaries"),
            {
                "action": "pay_selected",
                "salary_ids": [str(first_salary.pk)],
            },
        )

        self.assertRedirects(response, reverse("salaries"))
        first_salary.refresh_from_db()
        second_salary.refresh_from_db()
        self.assertEqual(first_salary.status, SalaryPayment.Status.PAID)
        self.assertEqual(second_salary.status, SalaryPayment.Status.PENDING)

    def test_manager_can_pay_all_pending_salary_records(self):
        manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Farm Manager")
        worker_role = Role.objects.create(code=Role.RoleCode.WORKER, name="Farm Worker")
        manager = get_user_model().objects.create_user(
            username="pay-all-manager",
            password="pass1234",
            role=manager_role,
        )
        first_worker = get_user_model().objects.create_user(
            username="pay-all-one",
            password="pass1234",
            role=worker_role,
        )
        second_worker = get_user_model().objects.create_user(
            username="pay-all-two",
            password="pass1234",
            role=worker_role,
        )
        paid_worker = get_user_model().objects.create_user(
            username="already-paid-worker",
            password="pass1234",
            role=worker_role,
        )
        first_salary = SalaryPayment.objects.create(
            employee=first_worker,
            period_month="2026-06",
            amount=Decimal("500000.00"),
            net_pay=Decimal("500000.00"),
            status=SalaryPayment.Status.PENDING,
            recorded_by=manager,
        )
        second_salary = SalaryPayment.objects.create(
            employee=second_worker,
            period_month="2026-06",
            amount=Decimal("600000.00"),
            net_pay=Decimal("600000.00"),
            status=SalaryPayment.Status.PENDING,
            recorded_by=manager,
        )
        already_paid = SalaryPayment.objects.create(
            employee=paid_worker,
            period_month="2026-06",
            amount=Decimal("700000.00"),
            net_pay=Decimal("700000.00"),
            status=SalaryPayment.Status.PAID,
            recorded_by=manager,
        )
        self.client.force_login(manager)

        response = self.client.post(reverse("salaries"), {"action": "pay_all"})

        self.assertRedirects(response, reverse("salaries"))
        first_salary.refresh_from_db()
        second_salary.refresh_from_db()
        already_paid.refresh_from_db()
        self.assertEqual(first_salary.status, SalaryPayment.Status.PAID)
        self.assertEqual(second_salary.status, SalaryPayment.Status.PAID)
        self.assertEqual(already_paid.status, SalaryPayment.Status.PAID)
