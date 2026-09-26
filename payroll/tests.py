from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from hr.models import SalaryAdvanceAllocation, StaffProfile, WelfareRequest
from payroll.models import HourlyWorkEntry, SalaryDisbursement, SalaryPayment
from payroll.services import (
    accrue_due_salary_liabilities,
    calculate_paye,
    calculate_salary_breakdown,
    calculate_uganda_lst,
    prepare_monthly_payroll,
    record_salary_disbursement,
)


class PayrollWorkflowTests(TestCase):
    def setUp(self):
        self.manager_role = Role.objects.create(code=Role.RoleCode.MANAGER, name="Farm Manager")
        self.worker_role = Role.objects.create(code=Role.RoleCode.WORKER, name="Farm Worker")
        self.manager = get_user_model().objects.create_user(
            username="payroll-manager", password="pass1234", role=self.manager_role
        )

    def make_worker(self, username, **profile_values):
        worker = get_user_model().objects.create_user(
            username=username,
            password="pass1234",
            role=self.worker_role,
        )
        defaults = {
            "monthly_salary": Decimal("1000000.00"),
            "pay_nssf": False,
            "pay_paye": False,
        }
        defaults.update(profile_values)
        StaffProfile.objects.create(user=worker, **defaults)
        return worker

    def test_uganda_lst_uses_july_to_october_installments_before_paye(self):
        self.assertEqual(calculate_uganda_lst(Decimal("150000"), "2026-07", eligible=True), Decimal("1250.00"))
        self.assertEqual(calculate_uganda_lst(Decimal("150000"), "2026-11", eligible=True), Decimal("0.00"))
        self.assertEqual(calculate_uganda_lst(Decimal("150000"), "2026-07", eligible=False), Decimal("0.00"))
        self.assertEqual(calculate_uganda_lst(Decimal("1000000"), "2026-07", eligible=True), Decimal("22500.00"))

        breakdown = calculate_salary_breakdown(
            Decimal("1000000"),
            Decimal("0"),
            pay_nssf=False,
            pay_paye=True,
            pay_lst=True,
            period_month="2026-07",
        )
        self.assertEqual(breakdown["lst_deduction"], Decimal("22500.00"))
        self.assertEqual(breakdown["paye_tax"], calculate_paye(Decimal("977500.00")))

    def test_hourly_entries_are_snapshotted_into_prepared_payroll(self):
        worker = self.make_worker(
            "hourly-worker",
            pay_basis=StaffProfile.PayBasis.HOURLY,
            hourly_rate=Decimal("10000.00"),
        )
        first = HourlyWorkEntry.objects.create(
            employee=worker,
            work_date=date(2099, 7, 3),
            hours_worked=Decimal("8"),
            hourly_rate=Decimal("10000"),
            recorded_by=self.manager,
        )
        second = HourlyWorkEntry.objects.create(
            employee=worker,
            work_date=date(2099, 7, 4),
            hours_worked=Decimal("4.5"),
            hourly_rate=Decimal("12000"),
            recorded_by=self.manager,
        )

        prepare_monthly_payroll("2099-07", created_by=self.manager)

        salary = SalaryPayment.objects.get(employee=worker, period_month="2099-07")
        self.assertEqual(salary.pay_basis, SalaryPayment.PayBasis.HOURLY)
        self.assertEqual(salary.hours_worked, Decimal("12.50"))
        self.assertEqual(salary.gross_salary, Decimal("134000.00"))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.salary_payment, salary)
        self.assertEqual(second.salary_payment, salary)

    def test_inactive_or_expired_staff_are_not_prepared_and_existing_draft_is_cancelled(self):
        inactive_worker = self.make_worker("inactive-worker")
        expired_worker = self.make_worker("expired-worker", contract_end_date=date(2099, 6, 30))

        prepare_monthly_payroll("2099-07", created_by=self.manager)
        draft = SalaryPayment.objects.get(employee=inactive_worker, period_month="2099-07")
        self.assertFalse(SalaryPayment.objects.filter(employee=expired_worker, period_month="2099-07").exists())

        profile = inactive_worker.staff_profile
        profile.employment_status = StaffProfile.EmploymentStatus.INACTIVE
        profile.save(update_fields=["employment_status"])
        with patch("payroll.services.timezone.localdate", return_value=date(2099, 7, 15)):
            summary = prepare_monthly_payroll("2099-07", created_by=self.manager)

        draft.refresh_from_db()
        self.assertEqual(summary["cancelled"], 1)
        self.assertEqual(draft.status, SalaryPayment.Status.CANCELLED)
        self.assertFalse(draft.liability_entry_reference)

    def test_issued_advance_is_recovered_evenly_across_selected_months(self):
        worker = self.make_worker("advance-worker")
        advance = WelfareRequest.objects.create(
            worker=worker,
            request_type=WelfareRequest.RequestType.SALARY_ADVANCE,
            title="School fees",
            details="Recover over two months",
            advance_amount=Decimal("100000.00"),
            advance_period_start=date(2099, 7, 1),
            advance_period_end=date(2099, 8, 31),
            advance_disbursed_on=date(2099, 6, 30),
            advance_payment_method="CASH",
            advance_payment_reference="ADV-100",
            advance_disbursed_by=self.manager,
            status=WelfareRequest.Status.MANAGER_APPROVED,
            manager=self.manager,
        )

        prepare_monthly_payroll("2099-07", created_by=self.manager)
        prepare_monthly_payroll("2099-08", created_by=self.manager)

        july = SalaryPayment.objects.get(employee=worker, period_month="2099-07")
        august = SalaryPayment.objects.get(employee=worker, period_month="2099-08")
        self.assertEqual(july.advances_deducted, Decimal("50000.00"))
        self.assertEqual(august.advances_deducted, Decimal("50000.00"))
        self.assertEqual(SalaryAdvanceAllocation.objects.filter(advance=advance).count(), 2)
        self.assertIsNone(advance.salary_payment_id)

    def test_unpaid_salary_becomes_liability_only_on_next_month_first_day(self):
        worker = self.make_worker("liability-worker")
        salary = SalaryPayment.objects.create(
            employee=worker,
            period_month="2099-06",
            amount=Decimal("100000"),
            gross_salary=Decimal("100000"),
            net_pay=Decimal("100000"),
            recorded_by=self.manager,
        )
        with patch("payroll.services.post_salary_accrual", return_value=Mock(reference="SAL-ACCRUAL-1")) as post:
            before = accrue_due_salary_liabilities(as_of=date(2099, 6, 30), created_by=self.manager)
            after = accrue_due_salary_liabilities(as_of=date(2099, 7, 1), created_by=self.manager)

        salary.refresh_from_db()
        self.assertEqual(before["accrued"], 0)
        self.assertEqual(after["accrued"], 1)
        self.assertEqual(salary.liability_entry_reference, "SAL-ACCRUAL-1")
        self.assertEqual(post.call_count, 1)

    def test_early_full_payment_posts_directly_without_a_salary_payable(self):
        worker = self.make_worker("direct-payment-worker")
        salary = SalaryPayment.objects.create(
            employee=worker,
            period_month="2099-07",
            amount=Decimal("100000"),
            gross_salary=Decimal("100000"),
            net_pay=Decimal("100000"),
            recorded_by=self.manager,
        )
        with patch("payroll.services._post_direct_salary_payment", return_value=Mock(reference="SAL-DIRECT-1")) as direct, patch(
            "payroll.services._post_salary_disbursement_settlement"
        ) as settlement:
            payment = record_salary_disbursement(
                salary.pk,
                amount=Decimal("100000"),
                payment_method=SalaryDisbursement.PaymentMethod.CASH,
                payment_reference="PAY-1",
                paid_on=date(2099, 7, 28),
                paid_by=self.manager,
            )

        salary.refresh_from_db()
        self.assertEqual(salary.status, SalaryPayment.Status.PAID)
        self.assertFalse(salary.liability_entry_reference)
        self.assertEqual(payment.accounting_entry_reference, "SAL-DIRECT-1")
        direct.assert_called_once()
        settlement.assert_not_called()

    def test_post_rollover_payment_clears_salaries_payable(self):
        worker = self.make_worker("settlement-worker")
        salary = SalaryPayment.objects.create(
            employee=worker,
            period_month="2099-07",
            amount=Decimal("100000"),
            gross_salary=Decimal("100000"),
            net_pay=Decimal("100000"),
            liability_entry_reference="SAL-ACCRUAL-2",
            recorded_by=self.manager,
        )
        with patch("payroll.services.ensure_salary_liability_accrual", return_value=Mock(reference="SAL-ACCRUAL-2")), patch(
            "payroll.services._post_salary_disbursement_settlement", return_value=Mock(reference="SAL-PAY-2")
        ) as settlement, patch("payroll.services._post_direct_salary_payment") as direct:
            record_salary_disbursement(
                salary.pk,
                amount=Decimal("100000"),
                payment_method=SalaryDisbursement.PaymentMethod.CASH,
                payment_reference="PAY-2",
                paid_on=date(2099, 8, 1),
                paid_by=self.manager,
            )

        settlement.assert_called_once()
        direct.assert_not_called()

    def test_hourly_wages_page_is_available_to_managers(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("hourly_wages"), {"month": "2099-07"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "payroll/hourly_wages.html")
        self.assertContains(response, "Hourly Wages")
