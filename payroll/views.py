from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.views import get_post_login_redirect
from .models import HourlyWorkEntry, SalaryBonus, SalaryDisbursement, SalaryPayment, SalaryPaymentEditLog
from .services import (
    accrue_due_salary_liabilities,
    calculate_gross_from_net,
    calculate_salary_breakdown,
    money,
    prepare_monthly_payroll,
    record_salary_disbursement,
    salary_payable_date,
    staff_users,
    validate_period_month,
)
from poultryiq.pagination import paginate


def _role_code(user) -> str:
    return (getattr(user.role, "code", "") or "").upper()


def _manager_only(request):
    if not request.user.is_authenticated:
        return redirect("login")
    if _role_code(request.user) not in ("MANAGER", "OWNER"):
        messages.error(request, "Access denied: Managers only.")
        return redirect(get_post_login_redirect(request.user))
    return None


def _staff_users(period_month=None):
    return staff_users(period_month)


def _money(value):
    return money(value)


def _current_period_month():
    return timezone.localdate().strftime("%Y-%m")


def _period_or_current(period_month):
    period_month = (period_month or _current_period_month()).strip()
    validate_period_month(period_month)
    return period_month


def _redirect_for_salary(salary):
    return redirect("pending_salaries", period_month=salary.period_month)


@login_required(login_url="login")
def salaries(request):
    denied = _manager_only(request)
    if denied:
        return denied
    return pending_salaries(request, _current_period_month())


@login_required(login_url="login")
def pending_salaries(request, period_month):
    denied = _manager_only(request)
    if denied:
        return denied

    try:
        period_month = _period_or_current(period_month)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("salaries")

    employees = staff_users(period_month)
    today = timezone.localdate()

    # This is deliberately run on each payroll visit as a resilience measure:
    # the daily command is the primary automation, but a missed run must not
    # leave overdue prepared salaries absent from Accounts Payable.
    liability_summary = accrue_due_salary_liabilities(as_of=today, created_by=request.user)

    if request.method == "POST":
        action = request.POST.get("action", "record").strip()
        if action == "generate_monthly":
            requested_month = request.POST.get("batch_month", period_month).strip()
            try:
                summary = prepare_monthly_payroll(requested_month, created_by=request.user)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
                return redirect("pending_salaries", period_month=period_month)

            # Preparing an old month after its rollover should immediately
            # recognise the overdue liability; preparing a current/future month
            # still posts nothing.
            rollover_summary = accrue_due_salary_liabilities(as_of=today, created_by=request.user)
            message = (
                "Monthly payroll prepared. No accounting entry was created by preparation. "
                f"Records ready: {summary['prepared']}. New records: {summary['created']}."
            )
            if summary["skipped_paid"]:
                message += f" Already-paid records skipped: {summary['skipped_paid']}."
            if summary["skipped_locked"]:
                message += f" Posted or part-paid records kept unchanged: {summary['skipped_locked']}."
            if summary.get("cancelled"):
                message += f" {summary['cancelled']} ineligible prepared record(s) were cancelled before posting."
            if rollover_summary["accrued"]:
                message += f" {rollover_summary['accrued']} overdue salary liability record(s) were posted."
            messages.success(request, message)
            return redirect("pending_salaries", period_month=summary["period_month"])

        if action in {"pay_selected", "pay_all"}:
            payment_method = request.POST.get("payment_method", "").strip()
            payment_reference = request.POST.get("payment_reference", "").strip()
            payment_date_raw = request.POST.get("payment_date", "").strip()
            paid_on = today
            if payment_date_raw:
                try:
                    paid_on = date.fromisoformat(payment_date_raw)
                except ValueError:
                    messages.error(request, "Please enter a valid payment date.")
                    return redirect("pending_salaries", period_month=period_month)

            payable_records = SalaryPayment.objects.filter(
                period_month=period_month,
                status__in=[SalaryPayment.Status.PREPARED, SalaryPayment.Status.PART_PAID],
            ).order_by("employee__username")
            if action == "pay_selected":
                selected_ids = request.POST.getlist("salary_ids")
                if not selected_ids:
                    messages.error(request, "Select at least one salary record to pay.")
                    return redirect("pending_salaries", period_month=period_month)
                records_to_pay = payable_records.filter(pk__in=selected_ids)
                if records_to_pay.count() != len(set(selected_ids)):
                    messages.error(request, "Only prepared or part-paid salaries can be paid.")
                    return redirect("pending_salaries", period_month=period_month)
            else:
                records_to_pay = payable_records

            paid_count = 0
            try:
                with transaction.atomic():
                    for salary in records_to_pay:
                        amount_raw = request.POST.get(f"payment_amount_{salary.pk}", "").strip()
                        amount = amount_raw or salary.outstanding_amount
                        record_salary_disbursement(
                            salary.pk,
                            amount=amount,
                            payment_method=payment_method,
                            payment_reference=payment_reference,
                            paid_on=paid_on,
                            paid_by=request.user,
                        )
                        paid_count += 1
            except (ValidationError, ValueError) as exc:
                messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
                return redirect("pending_salaries", period_month=period_month)

            if paid_count:
                messages.success(request, f"{paid_count} salary payment(s) recorded.")
            else:
                messages.info(request, "No prepared salary records were available to pay.")
            return redirect("pending_salaries", period_month=period_month)

        messages.error(request, "Unknown payroll action.")
        return redirect("pending_salaries", period_month=period_month)

    salary_records = list(
        SalaryPayment.objects.select_related("employee", "recorded_by")
        .prefetch_related("disbursements")
        .filter(period_month=period_month)
        .order_by("employee__username")
    )
    active_salary_records = [record for record in salary_records if not record.is_cancelled]
    total_due = sum((record.total_due for record in active_salary_records), Decimal("0.00"))
    total_paid = sum((record.amount_paid for record in active_salary_records), Decimal("0.00"))
    total_outstanding = sum((record.outstanding_amount for record in active_salary_records), Decimal("0.00"))
    prepared_count = sum(record.status == SalaryPayment.Status.PREPARED for record in salary_records)
    part_paid_count = sum(record.status == SalaryPayment.Status.PART_PAID for record in salary_records)
    paid_count = sum(record.status == SalaryPayment.Status.PAID for record in salary_records)
    cancelled_count = sum(record.status == SalaryPayment.Status.CANCELLED for record in salary_records)
    prepared_employee_ids = {record.employee_id for record in salary_records}
    liability_date = salary_payable_date(period_month)
    page_obj, querystring = paginate(request, salary_records, per_page=20)
    context = {
        "employees": employees,
        "salary_records": page_obj,
        "page_obj": page_obj,
        "querystring": querystring,
        "period_month": period_month,
        "current_period_month": _current_period_month(),
        "today": today,
        "liability_date": liability_date,
        "liability_due": today >= liability_date,
        "liability_summary": liability_summary,
        "prepared_count": prepared_count,
        "part_paid_count": part_paid_count,
        "paid_count": paid_count,
        "cancelled_count": cancelled_count,
        "payable_count": prepared_count + part_paid_count,
        "not_prepared_count": max(employees.count() - len(prepared_employee_ids), 0),
        "total_due": total_due,
        "total_paid": total_paid,
        "total_outstanding": total_outstanding,
        "payment_method_choices": SalaryDisbursement.PaymentMethod.choices,
    }
    return render(request, "payroll/salaries.html", context)


@login_required(login_url="login")
def salary_history(request):
    denied = _manager_only(request)
    if denied:
        return denied

    salary_records = SalaryPayment.objects.select_related("employee", "recorded_by").prefetch_related("disbursements").order_by(
        "-period_month", "employee__username"
    )
    page_obj, querystring = paginate(request, salary_records, per_page=25)
    return render(
        request,
        "payroll/salary_history.html",
        {
            "salary_records": page_obj,
            "page_obj": page_obj,
            "querystring": querystring,
            "current_period_month": _current_period_month(),
        },
    )


@login_required(login_url="login")
def hourly_wages(request):
    """Enter the dated work that will be used by an hourly payroll month."""
    denied = _manager_only(request)
    if denied:
        return denied

    requested_month = request.POST.get("period_month") if request.method == "POST" else request.GET.get("month")
    try:
        period_month = _period_or_current(requested_month)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("hourly_wages")

    eligible_employees = staff_users(period_month)
    hourly_employees = [
        employee
        for employee in eligible_employees
        if getattr(getattr(employee, "staff_profile", None), "pay_basis", "MONTHLY") == "HOURLY"
    ]

    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        if action == "remove_hourly_entry":
            entry = get_object_or_404(HourlyWorkEntry, pk=request.POST.get("entry_id"))
            if entry.salary_payment_id:
                messages.error(request, "This work entry is already part of prepared payroll and cannot be removed.")
            else:
                entry.delete()
                messages.success(request, "Hourly work entry removed.")
            return redirect(f"{reverse('hourly_wages')}?month={period_month}")

        if action == "add_hourly_entry":
            errors = []
            try:
                employee_id = int(request.POST.get("employee", ""))
            except (TypeError, ValueError):
                employee_id = None
            employee = eligible_employees.filter(pk=employee_id).first() if employee_id else None
            if employee not in hourly_employees:
                errors.append("Select an active hourly-wage employee for this payroll month.")

            try:
                work_date = date.fromisoformat(request.POST.get("work_date", ""))
                if work_date.strftime("%Y-%m") != period_month:
                    errors.append("The work date must be inside the selected payroll month.")
            except (TypeError, ValueError):
                work_date = None
                errors.append("Enter a valid work date.")
            try:
                hours_worked = money(Decimal(request.POST.get("hours_worked", "0")))
                if hours_worked <= 0:
                    errors.append("Hours worked must be greater than zero.")
            except (InvalidOperation, ValueError):
                hours_worked = Decimal("0.00")
                errors.append("Enter valid hours worked.")

            profile = getattr(employee, "staff_profile", None) if employee else None
            rate_raw = request.POST.get("hourly_rate", "").strip()
            try:
                hourly_rate = money(Decimal(rate_raw)) if rate_raw else money(getattr(profile, "hourly_rate", 0))
                if hourly_rate <= 0:
                    errors.append("Enter an hourly rate greater than zero.")
            except (InvalidOperation, ValueError):
                hourly_rate = Decimal("0.00")
                errors.append("Enter a valid hourly rate.")

            if employee and SalaryPayment.objects.filter(
                employee=employee,
                period_month=period_month,
            ).exclude(status=SalaryPayment.Status.PREPARED, liability_accrued_at__isnull=True).exists():
                errors.append("This employee's payroll for the month is already posted or paid and cannot receive new hours.")

            if errors:
                for error in errors:
                    messages.error(request, error)
            else:
                HourlyWorkEntry.objects.create(
                    employee=employee,
                    work_date=work_date,
                    hours_worked=hours_worked,
                    hourly_rate=hourly_rate,
                    notes=request.POST.get("notes", "").strip(),
                    recorded_by=request.user,
                )
                messages.success(request, "Hourly work recorded. Prepare this payroll month to include it.")
            return redirect(f"{reverse('hourly_wages')}?month={period_month}")

        messages.error(request, "Unknown hourly-work action.")
        return redirect(f"{reverse('hourly_wages')}?month={period_month}")

    year, month = [int(part) for part in period_month.split("-")]
    entries = list(
        HourlyWorkEntry.objects.select_related("employee", "salary_payment")
        .filter(work_date__gte=date(year, month, 1))
        .filter(work_date__lte=date(year, month, monthrange(year, month)[1]))
        .order_by("-work_date", "employee__username", "-work_entry_id")
    )
    total_hours = sum((entry.hours_worked for entry in entries), Decimal("0.00"))
    total_gross = sum((entry.gross_amount for entry in entries), Decimal("0.00"))
    return render(
        request,
        "payroll/hourly_wages.html",
        {
            "period_month": period_month,
            "current_period_month": _current_period_month(),
            "hourly_employees": hourly_employees,
            "entries": entries,
            "total_hours": total_hours,
            "total_gross": total_gross,
        },
    )


@login_required(login_url="login")
def salary_bonuses(request):
    denied = _manager_only(request)
    if denied:
        return denied

    employees = staff_users()
    if request.method == "POST":
        employee_ids = request.POST.getlist("bonus_employees")
        period_month = request.POST.get("bonus_month", "").strip()
        bonus_name = request.POST.get("bonus_name", "").strip()
        amount_raw = request.POST.get("bonus_amount", "0").strip()
        reason = request.POST.get("bonus_reason", "").strip()
        selected_employees = employees.filter(pk__in=employee_ids)
        errors = []
        amount = Decimal("0.00")

        if not employee_ids:
            errors.append("Please select at least one employee for the bonus.")
        elif selected_employees.count() != len(set(employee_ids)):
            errors.append("Please select valid employees for the bonus.")
        try:
            validate_period_month(period_month)
        except ValidationError as exc:
            errors.extend(exc.messages)
        if not bonus_name:
            errors.append("Bonus name is required.")
        try:
            amount = Decimal(amount_raw or "0")
            if amount <= 0:
                errors.append("Bonus amount must be greater than zero.")
        except (InvalidOperation, ValueError):
            errors.append("Please enter a valid bonus amount.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            bonuses = [
                SalaryBonus(
                    employee=employee,
                    period_month=period_month,
                    bonus_name=bonus_name,
                    amount=amount,
                    reason=reason,
                    granted_by=request.user,
                )
                for employee in selected_employees
            ]
            SalaryBonus.objects.bulk_create(bonuses)
            messages.success(request, f"Bonus granted to {len(bonuses)} employee(s). Prepare payroll to include it.")
            return redirect("salary_bonuses")

    bonuses = SalaryBonus.objects.select_related("employee", "granted_by").order_by(
        "-period_month", "employee__username", "-granted_at"
    )
    page_obj, querystring = paginate(request, bonuses, per_page=20)
    return render(
        request,
        "payroll/salary_bonuses.html",
        {
            "employees": employees,
            "bonuses": page_obj,
            "page_obj": page_obj,
            "querystring": querystring,
            "current_period_month": _current_period_month(),
        },
    )


@login_required(login_url="login")
def edit_salary(request, pk):
    denied = _manager_only(request)
    if denied:
        return denied

    salary = get_object_or_404(
        SalaryPayment.objects.select_related("employee", "recorded_by"),
        pk=pk,
    )

    # Payment state can only be changed through a SalaryDisbursement.  Once a
    # line has been posted or partly paid, changing its calculation would make
    # the payroll screen disagree with the journal, so it is locked here.
    if salary.status != SalaryPayment.Status.PREPARED or salary.liability_accrued_at:
        messages.error(
            request,
            "This salary is already posted or paid. Record any payment from the monthly payroll page instead.",
        )
        return _redirect_for_salary(salary)

    if request.method == "POST":
        gross_raw = request.POST.get("gross_salary", "0").strip()
        advances_raw = request.POST.get("advances_deducted", "0").strip()
        bonus_raw = request.POST.get("bonus_amount", "0").strip()
        net_raw = request.POST.get("net_pay", "0").strip()
        calculate_from = request.POST.get("calculate_from", "gross").strip()
        edit_reason = request.POST.get("edit_reason", "").strip()

        errors = []
        values = {
            "gross_salary": Decimal("0.00"),
            "advances_deducted": Decimal("0.00"),
            "bonus_amount": Decimal("0.00"),
            "net_pay": Decimal("0.00"),
        }
        for field_name, raw_value, label in [
            ("gross_salary", gross_raw, "gross salary"),
            ("advances_deducted", advances_raw, "advances deducted"),
            ("bonus_amount", bonus_raw, "bonus amount"),
            ("net_pay", net_raw, "net pay"),
        ]:
            try:
                value = Decimal(raw_value or "0")
                if value < 0:
                    errors.append(f"{label.title()} cannot be negative.")
                values[field_name] = money(value)
            except (InvalidOperation, ValueError):
                errors.append(f"Please enter a valid {label}.")

        if calculate_from not in {"gross", "net"}:
            errors.append("Invalid calculation option.")
        if not edit_reason:
            errors.append("Please enter a reason for editing this salary.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            profile = getattr(salary.employee, "staff_profile", None)
            pay_nssf = profile.pay_nssf if profile else True
            pay_paye = profile.pay_paye if profile else True
            pay_lst = profile.pay_lst if profile else False
            gross_salary = values["gross_salary"]
            if calculate_from == "net":
                gross_salary = calculate_gross_from_net(
                    values["net_pay"],
                    values["advances_deducted"],
                    bonuses=values["bonus_amount"],
                    pay_nssf=pay_nssf,
                    pay_paye=pay_paye,
                    pay_lst=pay_lst,
                    period_month=salary.period_month,
                )
            breakdown = calculate_salary_breakdown(
                gross_salary,
                values["advances_deducted"],
                bonuses=values["bonus_amount"],
                pay_nssf=pay_nssf,
                pay_paye=pay_paye,
                pay_lst=pay_lst,
                period_month=salary.period_month,
            )

            salary.gross_salary = breakdown["gross_salary"]
            salary.paye_tax = breakdown["paye_tax"]
            salary.nssf_employee = breakdown["nssf_employee"]
            salary.nssf_employer = breakdown["nssf_employer"]
            salary.lst_deduction = breakdown["lst_deduction"]
            salary.advances_deducted = breakdown["advances_deducted"]
            salary.bonus_amount = breakdown["bonus_amount"]
            salary.net_pay = breakdown["net_pay"]
            salary.amount = breakdown["net_pay"]
            salary.recorded_by = request.user
            salary.edit_reason = edit_reason
            salary.save(
                update_fields=[
                    "gross_salary",
                    "paye_tax",
                    "nssf_employee",
                    "nssf_employer",
                    "lst_deduction",
                    "advances_deducted",
                    "bonus_amount",
                    "net_pay",
                    "amount",
                    "recorded_by",
                    "edit_reason",
                    "updated_at",
                ]
            )
            SalaryPaymentEditLog.objects.create(salary=salary, edited_by=request.user, reason=edit_reason)
            messages.success(request, f"Salary updated for {salary.employee.display_name}.")
            return redirect("pending_salaries", period_month=salary.period_month)

    return render(
        request,
        "payroll/edit_salary.html",
        {
            "salary": salary,
        },
    )
