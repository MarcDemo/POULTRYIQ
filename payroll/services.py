from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from accounting.services import post_salary_accrual
from hr.models import WelfareRequest
from .models import SalaryBonus, SalaryPayment

User = get_user_model()


def money(value):
    return (value or Decimal("0.00")).quantize(Decimal("0.01"))


def validate_period_month(period_month):
    try:
        year, month = [int(part) for part in period_month.split("-")]
        if month < 1 or month > 12:
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise ValidationError("Payroll month must be in YYYY-MM format.")
    return year, month


def period_start(period_month):
    year, month = validate_period_month(period_month)
    return date(year, month, 1)


def period_end(period_month):
    year, month = validate_period_month(period_month)
    return date(year, month, 28)


def salary_payable_date(period_month):
    return period_end(period_month)


def staff_users():
    return (
        User.objects.select_related("role", "staff_profile")
        .filter(is_active=True, role__code__in=["WORKER", "SUPERVISOR", "MANAGER"])
        .exclude(role__code__in=["OWNER", "INVESTOR"])
        .exclude(role__name__icontains="investor")
        .order_by("first_name", "username")
    )


def calculate_paye(monthly_taxable_pay):
    income = money(monthly_taxable_pay)
    if income <= Decimal("235000"):
        return Decimal("0.00")
    if income <= Decimal("335000"):
        return money((income - Decimal("235000")) * Decimal("0.10"))
    if income <= Decimal("410000"):
        return money(Decimal("10000") + (income - Decimal("335000")) * Decimal("0.20"))

    paye = Decimal("25000") + (income - Decimal("410000")) * Decimal("0.30")
    if income > Decimal("10000000"):
        paye += (income - Decimal("10000000")) * Decimal("0.10")
    return money(paye)


def calculate_salary_breakdown(gross_salary, advances, bonuses=Decimal("0.00"), pay_nssf=True, pay_paye=True):
    gross = money(gross_salary)
    nssf_employee = money(gross * Decimal("0.05")) if pay_nssf else Decimal("0.00")
    nssf_employer = money(gross * Decimal("0.10")) if pay_nssf else Decimal("0.00")
    taxable_pay = max(gross - nssf_employee, Decimal("0.00"))
    paye_tax = calculate_paye(taxable_pay) if pay_paye else Decimal("0.00")
    advances = money(advances)
    bonuses = money(bonuses)
    net_pay = max(gross - nssf_employee - paye_tax - advances + bonuses, Decimal("0.00"))
    return {
        "gross_salary": gross,
        "nssf_employee": nssf_employee,
        "nssf_employer": nssf_employer,
        "paye_tax": paye_tax,
        "advances_deducted": advances,
        "bonus_amount": bonuses,
        "net_pay": money(net_pay),
    }


def calculate_gross_from_net(target_net, advances, bonuses=Decimal("0.00"), pay_nssf=True, pay_paye=True):
    target = money(target_net)
    advances = money(advances)
    bonuses = money(bonuses)
    low = Decimal("0.00")
    high = max(target + advances - bonuses, Decimal("1.00"))

    while calculate_salary_breakdown(high, advances, bonuses, pay_nssf=pay_nssf, pay_paye=pay_paye)["net_pay"] < target:
        high *= Decimal("2")

    for _ in range(48):
        mid = (low + high) / Decimal("2")
        net_pay = calculate_salary_breakdown(mid, advances, bonuses, pay_nssf=pay_nssf, pay_paye=pay_paye)["net_pay"]
        if net_pay < target:
            low = mid
        else:
            high = mid

    return money(high)


def approved_advances_for_period(employee, period_month):
    start_date = period_start(period_month)
    end_date = period_end(period_month)
    return WelfareRequest.objects.filter(
        worker=employee,
        request_type=WelfareRequest.RequestType.SALARY_ADVANCE,
        status=WelfareRequest.Status.MANAGER_APPROVED,
        salary_payment__isnull=True,
        advance_amount__gt=0,
        advance_period_start__lte=end_date,
        advance_period_end__gte=start_date,
    )


def bonus_total_for_period(employee, period_month):
    return SalaryBonus.objects.filter(employee=employee, period_month=period_month).aggregate(total=Sum("amount"))[
        "total"
    ] or Decimal("0.00")


@transaction.atomic
def prepare_monthly_payroll(period_month, *, created_by=None):
    validate_period_month(period_month)
    prepared = 0
    created = 0
    skipped_paid = 0

    for employee in staff_users():
        profile = getattr(employee, "staff_profile", None)
        gross_salary = profile.monthly_salary if profile else Decimal("0.00")
        pay_nssf = profile.pay_nssf if profile else True
        pay_paye = profile.pay_paye if profile else True
        advance_qs = approved_advances_for_period(employee, period_month)
        advances = advance_qs.aggregate(total=Sum("advance_amount"))["total"] or Decimal("0.00")
        bonuses = bonus_total_for_period(employee, period_month)
        breakdown = calculate_salary_breakdown(
            gross_salary,
            advances,
            bonuses=bonuses,
            pay_nssf=pay_nssf,
            pay_paye=pay_paye,
        )
        existing = SalaryPayment.objects.filter(employee=employee, period_month=period_month).first()
        if existing and existing.status == SalaryPayment.Status.PAID:
            skipped_paid += 1
            continue

        salary, was_created = SalaryPayment.objects.update_or_create(
            employee=employee,
            period_month=period_month,
            defaults={
                "amount": breakdown["net_pay"],
                "gross_salary": breakdown["gross_salary"],
                "paye_tax": breakdown["paye_tax"],
                "nssf_employee": breakdown["nssf_employee"],
                "nssf_employer": breakdown["nssf_employer"],
                "advances_deducted": breakdown["advances_deducted"],
                "bonus_amount": breakdown["bonus_amount"],
                "net_pay": breakdown["net_pay"],
                "payment_date": period_end(period_month),
                "status": SalaryPayment.Status.PENDING,
                "recorded_by": created_by,
            },
        )
        advance_qs.update(salary_payment=salary)
        post_salary_accrual(salary, created_by=created_by)
        prepared += 1
        created += 1 if was_created else 0

    return {
        "period_month": period_month,
        "prepared": prepared,
        "created": created,
        "skipped_paid": skipped_paid,
    }
