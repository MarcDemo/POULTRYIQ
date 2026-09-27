from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from accounting.services import (
    ensure_payroll_accounts,
    payment_account_for_method,
    post_journal_entry,
    post_salary_accrual,
)
from hr.models import SalaryAdvanceAllocation, StaffProfile, WelfareRequest
from .models import HourlyWorkEntry, SalaryBonus, SalaryDisbursement, SalaryPayment

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
    return date(year, month, monthrange(year, month)[1])


def salary_payable_date(period_month):
    """The day unpaid payroll becomes a Salaries Payable liability."""
    year, month = validate_period_month(period_month)
    if month == 12:
        return date(year + 1, 1, 1)
    return date(year, month + 1, 1)


def staff_users(period_month=None):
    """Return staff eligible for a payroll month.

    Old user records without a staff profile remain available so a manager can
    correct their payroll rather than silently losing them.  A profile marked
    inactive, however, is excluded.  A contract that ended before the first
    day of the selected payroll month is also excluded.
    """
    employees = (
        User.objects.select_related("role", "staff_profile")
        .filter(is_active=True, role__code__in=["WORKER", "SUPERVISOR", "MANAGER"])
        .exclude(role__code__in=["OWNER", "INVESTOR"])
        .exclude(role__name__icontains="investor")
    )
    profile_is_active = Q(staff_profile__isnull=True) | Q(
        staff_profile__employment_status=StaffProfile.EmploymentStatus.ACTIVE
    )
    employees = employees.filter(profile_is_active)
    if period_month:
        month_start = period_start(period_month)
        employees = employees.filter(
            Q(staff_profile__isnull=True)
            | Q(staff_profile__contract_end_date__isnull=True)
            | Q(staff_profile__contract_end_date__gte=month_start)
        )
    return employees.order_by("first_name", "username")


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


UGANDA_LST_ANNUAL_BANDS = (
    (Decimal("100000"), Decimal("0")),
    (Decimal("200000"), Decimal("5000")),
    (Decimal("300000"), Decimal("10000")),
    (Decimal("400000"), Decimal("20000")),
    (Decimal("500000"), Decimal("30000")),
    (Decimal("600000"), Decimal("40000")),
    (Decimal("700000"), Decimal("60000")),
    (Decimal("800000"), Decimal("70000")),
    (Decimal("900000"), Decimal("80000")),
    (Decimal("1000000"), Decimal("90000")),
)
UGANDA_LST_TOP_RATE = Decimal("100000")
UGANDA_LST_INSTALLMENT_MONTHS = (7, 8, 9, 10)


def calculate_uganda_lst(monthly_income, period_month, *, eligible=False):
    """Calculate the Uganda Local Service Tax installment for one payroll month.

    The statutory annual amount is banded by monthly income.  Employers deduct
    it in four equal installments during July through October of the local
    government financial year.  The employee must be marked as LST-eligible
    in onboarding before a deduction can be made.
    """
    if not eligible:
        return Decimal("0.00")
    _year, month = validate_period_month(period_month)
    if month not in UGANDA_LST_INSTALLMENT_MONTHS:
        return Decimal("0.00")
    income = money(monthly_income)
    annual_amount = UGANDA_LST_TOP_RATE
    for ceiling, amount in UGANDA_LST_ANNUAL_BANDS:
        if income <= ceiling:
            annual_amount = amount
            break
    return money(annual_amount / Decimal(len(UGANDA_LST_INSTALLMENT_MONTHS)))


def calculate_salary_breakdown(
    gross_salary,
    advances,
    bonuses=Decimal("0.00"),
    pay_nssf=True,
    pay_paye=True,
    pay_lst=False,
    period_month=None,
):
    gross = money(gross_salary)
    nssf_employee = money(gross * Decimal("0.05")) if pay_nssf else Decimal("0.00")
    nssf_employer = money(gross * Decimal("0.10")) if pay_nssf else Decimal("0.00")
    advances = money(advances)
    bonuses = money(bonuses)
    # Local Service Tax uses the employee's gross monthly pay for the band.
    # In the payroll calculation it is deducted before PAYE, as required for
    # the Uganda withholding sequence.
    lst_deduction = calculate_uganda_lst(
        gross,
        period_month,
        eligible=pay_lst,
    ) if period_month else Decimal("0.00")
    taxable_pay = max(gross - nssf_employee - lst_deduction, Decimal("0.00"))
    paye_tax = calculate_paye(taxable_pay) if pay_paye else Decimal("0.00")
    net_pay = max(gross - nssf_employee - paye_tax - lst_deduction - advances + bonuses, Decimal("0.00"))
    return {
        "gross_salary": gross,
        "nssf_employee": nssf_employee,
        "nssf_employer": nssf_employer,
        "paye_tax": paye_tax,
        "lst_deduction": lst_deduction,
        "advances_deducted": advances,
        "bonus_amount": bonuses,
        "net_pay": money(net_pay),
    }


def calculate_gross_from_net(
    target_net,
    advances,
    bonuses=Decimal("0.00"),
    pay_nssf=True,
    pay_paye=True,
    pay_lst=False,
    period_month=None,
):
    target = money(target_net)
    advances = money(advances)
    bonuses = money(bonuses)
    low = Decimal("0.00")
    high = max(target + advances - bonuses, Decimal("1.00"))

    while calculate_salary_breakdown(
        high,
        advances,
        bonuses,
        pay_nssf=pay_nssf,
        pay_paye=pay_paye,
        pay_lst=pay_lst,
        period_month=period_month,
    )["net_pay"] < target:
        high *= Decimal("2")

    for _ in range(48):
        mid = (low + high) / Decimal("2")
        net_pay = calculate_salary_breakdown(
            mid,
            advances,
            bonuses,
            pay_nssf=pay_nssf,
            pay_paye=pay_paye,
            pay_lst=pay_lst,
            period_month=period_month,
        )["net_pay"]
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
        advance_amount__gt=0,
    ).filter(
        # New workflow: only money actually issued to the employee is a staff
        # advance receivable.  The legacy link remains visible for historical
        # payroll records made before issuance was recorded separately.
        Q(advance_disbursed_on__isnull=False) | Q(salary_payment__isnull=False),
    ).filter(
        Q(salary_payment__isnull=False)
        | Q(advance_period_start__lte=end_date, advance_period_end__gte=start_date)
    ).order_by("advance_period_start", "request_id")


def _months_inclusive(start_date, end_date):
    """Return payroll month keys touched by an advance recovery period."""
    current = date(start_date.year, start_date.month, 1)
    final = date(end_date.year, end_date.month, 1)
    months = []
    while current <= final:
        months.append(current.strftime("%Y-%m"))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return months


def _scheduled_advance_recovery(advance, period_month):
    """Return this month's planned recovery amount for one issued advance.

    A recovery period spanning several months divides the approved advance
    evenly, assigning any rounding remainder to the final month.  That makes
    the plan stable even if managers prepare the months out of order.
    """
    total = money(advance.advance_amount)
    if total <= 0:
        return Decimal("0.00")
    if not advance.advance_period_start or not advance.advance_period_end:
        return total
    months = _months_inclusive(advance.advance_period_start, advance.advance_period_end)
    if period_month not in months:
        return Decimal("0.00")
    monthly_share = money(total / Decimal(len(months)))
    if period_month != months[-1]:
        return monthly_share
    return money(total - (monthly_share * Decimal(len(months) - 1)))


def allocate_advances_for_salary(salary, *, created_by=None, maximum_recovery=None):
    """Allocate issued staff advances to a prepared payroll line.

    Each allocation is immutable payroll evidence: repeated preparation keeps
    the existing amount, while a new issued advance can add its own scheduled
    recovery to the same month.  Legacy one-to-one advance links are read as a
    full historical allocation and are not duplicated.
    """
    period_month = salary.period_month
    advances = approved_advances_for_period(salary.employee, period_month)
    allocated_total = Decimal("0.00")
    maximum_recovery = money(maximum_recovery) if maximum_recovery is not None else None

    for advance in advances:
        existing = SalaryAdvanceAllocation.objects.filter(
            advance=advance,
            salary_payment=salary,
        ).first()
        if existing:
            allocated_total += existing.amount
            continue
        if advance.salary_payment_id and advance.salary_payment_id != salary.pk:
            # A historic whole-advance link belongs to a different payroll
            # line and must not be recovered a second time.
            continue
        if advance.salary_payment_id == salary.pk:
            # Before allocation rows were introduced, a linked request meant
            # the entire advance was recovered in this payroll line.
            allocated_total += money(advance.advance_amount)
            continue

        scheduled_amount = _scheduled_advance_recovery(advance, period_month)
        if scheduled_amount <= 0:
            continue
        other_allocations = advance.salary_advance_allocations.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        remaining = max(money(advance.advance_amount) - money(other_allocations), Decimal("0.00"))
        if maximum_recovery is not None:
            remaining_capacity = max(maximum_recovery - allocated_total, Decimal("0.00"))
            allocation_amount = min(scheduled_amount, remaining, remaining_capacity)
        else:
            allocation_amount = min(scheduled_amount, remaining)
        if allocation_amount <= 0:
            continue
        allocation = SalaryAdvanceAllocation.objects.create(
            advance=advance,
            salary_payment=salary,
            amount=allocation_amount,
            allocated_by=created_by,
        )
        allocated_total += allocation.amount

    return money(allocated_total)


def hourly_entries_for_period(employee, period_month, *, salary=None):
    """Return unallocated work plus entries already linked to this salary."""
    entries = HourlyWorkEntry.objects.filter(
        employee=employee,
        work_date__gte=period_start(period_month),
        work_date__lte=period_end(period_month),
    )
    if salary is None:
        return entries.filter(salary_payment__isnull=True)
    return entries.filter(Q(salary_payment__isnull=True) | Q(salary_payment=salary))


def hourly_work_totals(employee, period_month, *, salary=None):
    entries = list(hourly_entries_for_period(employee, period_month, salary=salary))
    hours = sum((entry.hours_worked for entry in entries), Decimal("0.00"))
    gross = sum((entry.gross_amount for entry in entries), Decimal("0.00"))
    return money(hours), money(gross), entries


def bonus_total_for_period(employee, period_month):
    return SalaryBonus.objects.filter(employee=employee, period_month=period_month).aggregate(total=Sum("amount"))[
        "total"
    ] or Decimal("0.00")


def _cancel_ineligible_prepared_salaries(period_month, eligible_employee_ids, *, cancelled_by=None):
    """Cancel a not-yet-posted salary when its employee becomes ineligible.

    We retain the row rather than deleting it, which makes the reason visible
    in salary history.  Once money has been paid or the salary has reached the
    next month's liability date, financial history is locked and never changed
    by a staff-profile update.
    """
    if timezone.localdate() >= salary_payable_date(period_month):
        return 0
    now = timezone.now()
    cancellable = SalaryPayment.objects.filter(
        period_month=period_month,
        status=SalaryPayment.Status.PREPARED,
        amount_paid=Decimal("0.00"),
        liability_accrued_at__isnull=True,
        disbursements__isnull=True,
    ).exclude(employee_id__in=eligible_employee_ids)
    return cancellable.update(
        status=SalaryPayment.Status.CANCELLED,
        cancelled_at=now,
        cancellation_reason="Employee is inactive or their contract ended before this payroll month.",
        recorded_by=cancelled_by,
    )


@transaction.atomic
def prepare_monthly_payroll(period_month, *, created_by=None):
    """Prepare a selected payroll month without creating an accounting entry.

    Salaries become accounting liabilities only on the first day of the
    following month.  Until then a manager can prepare, review, edit, or pay a
    complete salary directly.  Employee eligibility and hourly work are
    evaluated for the selected month rather than the calendar day of the run.
    """
    validate_period_month(period_month)
    prepared = 0
    created = 0
    skipped_paid = 0
    skipped_locked = 0
    employees = list(staff_users(period_month))
    eligible_employee_ids = {employee.pk for employee in employees}
    cancelled = _cancel_ineligible_prepared_salaries(
        period_month,
        eligible_employee_ids,
        cancelled_by=created_by,
    )

    for employee in employees:
        profile = getattr(employee, "staff_profile", None)
        pay_basis = getattr(profile, "pay_basis", StaffProfile.PayBasis.MONTHLY) if profile else StaffProfile.PayBasis.MONTHLY
        pay_nssf = getattr(profile, "pay_nssf", True) if profile else True
        pay_paye = getattr(profile, "pay_paye", True) if profile else True
        pay_lst = getattr(profile, "pay_lst", False) if profile else False
        bonuses = bonus_total_for_period(employee, period_month)
        existing = SalaryPayment.objects.filter(employee=employee, period_month=period_month).first()
        if existing and existing.status == SalaryPayment.Status.PAID:
            skipped_paid += 1
            continue
        if existing and (
            existing.status == SalaryPayment.Status.PART_PAID
            or existing.liability_accrued_at is not None
        ):
            skipped_locked += 1
            continue

        # A salary record is created before advance allocation because the
        # allocation is auditable and deliberately points at a payroll month.
        salary, was_created = SalaryPayment.objects.get_or_create(
            employee=employee,
            period_month=period_month,
            defaults={
                "amount": Decimal("0.00"),
                "net_pay": Decimal("0.00"),
                "status": SalaryPayment.Status.PREPARED,
                "recorded_by": created_by,
            },
        )
        if pay_basis == StaffProfile.PayBasis.HOURLY:
            hours_worked, gross_salary, hourly_entries = hourly_work_totals(
                employee,
                period_month,
                salary=salary,
            )
            hourly_rate = money(gross_salary / hours_worked) if hours_worked else money(
                getattr(profile, "hourly_rate", Decimal("0.00"))
            )
        else:
            hours_worked = Decimal("0.00")
            hourly_entries = []
            hourly_rate = Decimal("0.00")
            gross_salary = money(getattr(profile, "monthly_salary", Decimal("0.00")))

        without_advance = calculate_salary_breakdown(
            gross_salary,
            Decimal("0.00"),
            bonuses=bonuses,
            pay_nssf=pay_nssf,
            pay_paye=pay_paye,
            pay_lst=pay_lst,
            period_month=period_month,
        )
        advances = allocate_advances_for_salary(
            salary,
            created_by=created_by,
            maximum_recovery=without_advance["net_pay"],
        )
        breakdown = calculate_salary_breakdown(
            gross_salary,
            advances,
            bonuses=bonuses,
            pay_nssf=pay_nssf,
            pay_paye=pay_paye,
            pay_lst=pay_lst,
            period_month=period_month,
        )

        salary.amount = breakdown["net_pay"]
        salary.gross_salary = breakdown["gross_salary"]
        salary.paye_tax = breakdown["paye_tax"]
        salary.nssf_employee = breakdown["nssf_employee"]
        salary.nssf_employer = breakdown["nssf_employer"]
        salary.lst_deduction = breakdown["lst_deduction"]
        salary.advances_deducted = breakdown["advances_deducted"]
        salary.bonus_amount = breakdown["bonus_amount"]
        salary.net_pay = breakdown["net_pay"]
        salary.pay_basis = pay_basis
        salary.hours_worked = hours_worked
        salary.hourly_rate = hourly_rate
        salary.payment_date = None
        salary.status = SalaryPayment.Status.PREPARED
        salary.cancelled_at = None
        salary.cancellation_reason = ""
        salary.recorded_by = created_by
        salary.save()
        if hourly_entries:
            HourlyWorkEntry.objects.filter(pk__in=[entry.pk for entry in hourly_entries]).update(salary_payment=salary)
        prepared += 1
        created += 1 if was_created else 0

    return {
        "period_month": period_month,
        "prepared": prepared,
        "created": created,
        "cancelled": cancelled,
        "skipped_paid": skipped_paid,
        "skipped_locked": skipped_locked,
    }


def _normalise_payment_method(payment_method):
    method = (payment_method or "").strip().upper().replace(" ", "_")
    aliases = {
        "MOMO": SalaryDisbursement.PaymentMethod.MOBILE_MONEY,
        "MOBILEMONEY": SalaryDisbursement.PaymentMethod.MOBILE_MONEY,
        "CHECK": SalaryDisbursement.PaymentMethod.CHEQUE,
    }
    method = aliases.get(method, method)
    valid_methods = {value for value, _label in SalaryDisbursement.PaymentMethod.choices}
    if method not in valid_methods:
        raise ValidationError("Select a valid salary payment method.")
    return method


def _salary_total_due(salary):
    return money(salary.net_pay or salary.amount)


def _salary_outstanding(salary):
    return max(_salary_total_due(salary) - money(salary.amount_paid), Decimal("0.00"))


def _accrual_reference(salary):
    return f"SAL-ACCRUAL-{salary.pk}"


def _existing_salary_accrual(salary):
    """Find an accrual posted before this payroll enhancement was installed."""
    from accounting.models import JournalEntry

    return JournalEntry.objects.filter(
        reference=_accrual_reference(salary),
        status=JournalEntry.Status.POSTED,
    ).first()


def ensure_salary_liability_accrual(salary, *, as_of=None, created_by=None, force=False):
    """Post the one salary accrual required before a salary can be settled.

    ``force`` is used when a manager actually pays a prepared salary before
    the month rollover.  Preparation alone never calls this function.
    """
    as_of = as_of or timezone.localdate()
    if not force and as_of < salary_payable_date(salary.period_month):
        return None

    if salary.liability_entry_reference:
        existing_entry = _existing_salary_accrual(salary)
        if existing_entry:
            return existing_entry
        # A voided/deleted legacy entry must not permanently prevent a correct
        # re-accrual.  Clear the stale marker before checking/posting again.
        salary.liability_entry_reference = ""
        salary.liability_accrued_at = None

    existing_entry = _existing_salary_accrual(salary)
    if existing_entry:
        salary.liability_entry_reference = existing_entry.reference
        salary.liability_accrued_at = salary.liability_accrued_at or timezone.now()
        salary.save(update_fields=["liability_entry_reference", "liability_accrued_at", "updated_at"])
        return existing_entry

    if _salary_total_due(salary) <= 0:
        return None

    # The existing accounting service uses salary.payment_date as the entry
    # date.  Keep the change in memory until the surrounding payment or
    # rollover transaction succeeds.
    original_payment_date = salary.payment_date
    salary.payment_date = as_of
    entry = post_salary_accrual(salary, created_by=created_by)
    salary.payment_date = original_payment_date
    if entry is None:
        return None

    salary.liability_entry_reference = entry.reference
    salary.liability_accrued_at = timezone.now()
    salary.save(update_fields=["liability_entry_reference", "liability_accrued_at", "updated_at"])
    return entry


def accrue_due_salary_liabilities(*, as_of=None, created_by=None):
    """Accrue prepared/part-paid payroll as a liability from the next month.

    This function is safe to run repeatedly.  It is used by the daily command
    and when a manager opens payroll, so a missed scheduler run cannot leave a
    visible past-due payroll amount off the balance sheet indefinitely.
    """
    as_of = as_of or timezone.localdate()
    summary = {"accrued": 0, "already_accrued": 0, "skipped": 0, "errors": []}
    eligible_statuses = [SalaryPayment.Status.PREPARED, SalaryPayment.Status.PART_PAID]
    salaries = SalaryPayment.objects.filter(status__in=eligible_statuses).order_by("period_month", "pk")

    for salary_id in salaries.values_list("pk", flat=True):
        try:
            with transaction.atomic():
                salary = SalaryPayment.objects.select_for_update().get(pk=salary_id)
                if _salary_outstanding(salary) <= 0:
                    summary["skipped"] += 1
                    continue
                if as_of < salary_payable_date(salary.period_month):
                    summary["skipped"] += 1
                    continue
                was_accrued = bool(salary.liability_entry_reference or _existing_salary_accrual(salary))
                entry = ensure_salary_liability_accrual(salary, as_of=as_of, created_by=created_by)
                if entry is None:
                    summary["skipped"] += 1
                elif was_accrued:
                    summary["already_accrued"] += 1
                else:
                    summary["accrued"] += 1
        except Exception as exc:  # Keep other employees' liabilities moving.
            summary["errors"].append(f"Salary #{salary_id}: {exc}")
    return summary


def _post_salary_disbursement_settlement(disbursement, *, created_by=None):
    """Settle the amount actually disbursed from Salaries Payable.

    Accounting's legacy ``post_salary_payment`` settles a whole salary using
    cash.  This payroll-specific wrapper supports a selected payment method
    and a genuine part-payment without changing accounting's public API.
    """
    accounts = ensure_payroll_accounts()
    payment_account = payment_account_for_method(disbursement.payment_method)
    if payment_account is None and disbursement.payment_method == SalaryDisbursement.PaymentMethod.CASH:
        payment_account = accounts["cash"]
    if payment_account is None:
        raise ValidationError(
            f"{disbursement.get_payment_method_display()} is not mapped to an active accounting payment account."
        )

    salary = disbursement.salary
    employee_name = getattr(salary.employee, "display_name", str(salary.employee))
    reference = f"SAL-PAY-{salary.pk}-{disbursement.pk}"
    return post_journal_entry(
        entry_date=disbursement.paid_on,
        reference=reference,
        description=f"Salary paid to {employee_name} - {salary.period_month}",
        lines=[
            {"account": accounts["salary_payable"], "debit": disbursement.amount, "memo": "Clear salary payable"},
            {"account": payment_account, "credit": disbursement.amount, "memo": "Net salary paid"},
        ],
        source=disbursement,
        created_by=created_by or disbursement.paid_by,
        dedupe_source=False,
    )


def _salary_breakdown_for_direct_payment(salary):
    """Match the payroll accounting amounts without creating a payable."""
    gross_salary = money(salary.gross_salary or salary.amount)
    paye_tax = money(salary.paye_tax)
    nssf_employee = money(salary.nssf_employee)
    nssf_employer = money(salary.nssf_employer)
    lst_deduction = money(getattr(salary, "lst_deduction", Decimal("0.00")))
    advances = money(salary.advances_deducted)
    bonuses = money(salary.bonus_amount)
    calculated_net = money(
        max(gross_salary + bonuses - nssf_employee - paye_tax - lst_deduction - advances, Decimal("0.00"))
    )
    net_pay = money(salary.net_pay or salary.amount or calculated_net)
    # Payroll data is sometimes imported with a stale net value.  Accounting
    # must use the balanced calculation, just as accounting.post_salary_accrual
    # does for rollover liabilities.
    if net_pay != calculated_net:
        net_pay = calculated_net
    return {
        "gross_salary": gross_salary,
        "paye_tax": paye_tax,
        "lst_deduction": lst_deduction,
        "nssf_employee": nssf_employee,
        "nssf_employer": nssf_employer,
        "advances": advances,
        "bonuses": bonuses,
        "net_pay": net_pay,
    }


def _post_direct_salary_payment(disbursement, *, created_by=None):
    """Post an actual pre-rollover salary payment without Salaries Payable.

    Preparation is non-accounting.  If the entire prepared salary is actually
    paid before the next month's first day, this records the salary expense,
    deductions and chosen cash/bank/mobile-money payment in one journal.
    """
    accounts = ensure_payroll_accounts()
    payment_account = payment_account_for_method(disbursement.payment_method)
    if payment_account is None and disbursement.payment_method == SalaryDisbursement.PaymentMethod.CASH:
        payment_account = accounts["cash"]
    if payment_account is None:
        raise ValidationError(
            f"{disbursement.get_payment_method_display()} is not mapped to an active accounting payment account."
        )

    salary = disbursement.salary
    amounts = _salary_breakdown_for_direct_payment(salary)
    if disbursement.amount != amounts["net_pay"]:
        raise ValidationError("A pre-rollover salary payment must settle the full net salary.")

    employee_name = getattr(salary.employee, "display_name", str(salary.employee))
    lines = []
    if amounts["gross_salary"] > 0:
        lines.append({"account": accounts["salary_expense"], "debit": amounts["gross_salary"], "memo": "Gross salary"})
    if amounts["bonuses"] > 0:
        lines.append({"account": accounts["bonus_expense"], "debit": amounts["bonuses"], "memo": "Bonus"})
    if amounts["nssf_employer"] > 0:
        lines.append(
            {"account": accounts["employer_nssf_expense"], "debit": amounts["nssf_employer"], "memo": "Employer NSSF"}
        )
    if amounts["advances"] > 0:
        lines.append(
            {"account": accounts["prepaid_salaries"], "credit": amounts["advances"], "memo": "Salary advance recovered"}
        )
    if amounts["paye_tax"] > 0:
        lines.append({"account": accounts["paye_payable"], "credit": amounts["paye_tax"], "memo": "PAYE withheld"})
    if amounts["lst_deduction"] > 0:
        lines.append(
            {"account": accounts["lst_payable"], "credit": amounts["lst_deduction"], "memo": "Local Service Tax withheld"}
        )
    total_nssf = amounts["nssf_employee"] + amounts["nssf_employer"]
    if total_nssf > 0:
        lines.append({"account": accounts["nssf_payable"], "credit": total_nssf, "memo": "NSSF payable"})
    if amounts["net_pay"] > 0:
        lines.append({"account": payment_account, "credit": amounts["net_pay"], "memo": "Net salary paid"})

    return post_journal_entry(
        entry_date=disbursement.paid_on,
        reference=f"SAL-DIRECT-{salary.pk}-{disbursement.pk}",
        description=f"Salary paid to {employee_name} - {salary.period_month}",
        lines=lines,
        source=disbursement,
        created_by=created_by or disbursement.paid_by,
        dedupe_source=False,
    )


@transaction.atomic
def record_salary_disbursement(
    salary_id,
    *,
    amount,
    payment_method,
    payment_reference,
    paid_on=None,
    paid_by=None,
):
    """Record a full or partial salary payment and update its display state."""
    salary = SalaryPayment.objects.select_for_update().select_related("employee").get(pk=salary_id)
    if salary.status == SalaryPayment.Status.PAID:
        raise ValidationError("This salary is already fully paid.")
    if salary.status == SalaryPayment.Status.CANCELLED:
        raise ValidationError("This salary was cancelled because the employee is not eligible for this payroll month.")

    try:
        amount = money(Decimal(str(amount)))
    except Exception as exc:
        raise ValidationError("Enter a valid payment amount.") from exc
    if amount <= 0:
        raise ValidationError("Payment amount must be greater than zero.")

    outstanding = _salary_outstanding(salary)
    if amount > outstanding:
        raise ValidationError(f"Payment cannot exceed the outstanding salary of {outstanding}.")

    payment_method = _normalise_payment_method(payment_method)
    payment_reference = (payment_reference or "").strip()
    if not payment_reference:
        raise ValidationError("Enter a payment reference or receipt number.")
    if paid_by is None:
        raise ValidationError("A user is required to record a salary payment.")
    paid_on = paid_on or timezone.localdate()

    liability_is_due = paid_on >= salary_payable_date(salary.period_month)
    has_salary_payable = bool(salary.liability_entry_reference or _existing_salary_accrual(salary))
    if not liability_is_due and not has_salary_payable and amount != outstanding:
        raise ValidationError(
            "Before the next month's first day, a salary payment must settle the full net salary. "
            "Part-payments are available once an unpaid salary has moved to Salaries Payable."
        )

    # A prepared salary is non-accounting.  Once it rolls over unpaid, it is a
    # liability and payment clears that liability.  A genuinely early full
    # payment instead posts direct expense/deductions/cash, without creating a
    # temporary Salary Payable entry.
    if liability_is_due or has_salary_payable:
        ensure_salary_liability_accrual(salary, as_of=paid_on, created_by=paid_by)
        if not salary.liability_entry_reference:
            raise ValidationError("Salary payable could not be created before this payment was recorded.")
        posting_function = _post_salary_disbursement_settlement
    else:
        posting_function = _post_direct_salary_payment

    disbursement = SalaryDisbursement.objects.create(
        salary=salary,
        amount=amount,
        payment_method=payment_method,
        payment_reference=payment_reference,
        paid_on=paid_on,
        paid_by=paid_by,
    )
    entry = posting_function(disbursement, created_by=paid_by)
    disbursement.accounting_entry_reference = entry.reference if entry else ""
    disbursement.save(update_fields=["accounting_entry_reference"])

    salary.amount_paid = money(salary.amount_paid + amount)
    salary.payment_date = paid_on
    salary.recorded_by = paid_by
    salary.status = (
        SalaryPayment.Status.PAID
        if _salary_outstanding(salary) <= 0
        else SalaryPayment.Status.PART_PAID
    )
    salary.save(update_fields=["amount_paid", "payment_date", "recorded_by", "status", "updated_at"])
    return disbursement
