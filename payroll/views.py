from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.views import get_post_login_redirect
from accounting.services import post_salary_accrual, post_salary_payment
from .models import SalaryBonus, SalaryPayment, SalaryPaymentEditLog
from .services import (
    calculate_gross_from_net,
    calculate_paye,
    calculate_salary_breakdown,
    money,
    period_end,
    prepare_monthly_payroll,
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


def _staff_users():
    return staff_users()


def _money(value):
    return money(value)


def _period_end(period_month):
    return period_end(period_month)


def _salary_payable_date(period_month):
    return salary_payable_date(period_month)


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

    employees = staff_users()
    salary_records = SalaryPayment.objects.select_related("employee", "recorded_by").filter(
        period_month=period_month,
        status=SalaryPayment.Status.PENDING,
    ).order_by("employee__username")
    current_period_month = _current_period_month()
    pending_visible_from = salary_payable_date(period_month)
    hide_current_pending = period_month == current_period_month and timezone.localdate() < pending_visible_from
    if hide_current_pending:
        salary_records = salary_records.none()

    if request.method == "POST":
        action = request.POST.get("action", "record").strip()
        if action == "generate_monthly":
            requested_month = request.POST.get("batch_month", period_month).strip()
            try:
                summary = prepare_monthly_payroll(requested_month, created_by=request.user)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
                return redirect("pending_salaries", period_month=period_month)

            message = (
                "Monthly payroll prepared and accrued. "
                f"Records ready: {summary['prepared']}. New records: {summary['created']}."
            )
            if summary["skipped_paid"]:
                message += f" Already-paid records skipped: {summary['skipped_paid']}."
            messages.success(request, message)
            return redirect("pending_salaries", period_month=summary["period_month"])

        if action in {"pay_selected", "pay_all"}:
            if action == "pay_selected":
                selected_ids = request.POST.getlist("salary_ids")
                if not selected_ids:
                    messages.error(request, "Select at least one salary record to pay.")
                    return redirect("pending_salaries", period_month=period_month)
                records_to_pay = salary_records.filter(pk__in=selected_ids)
            else:
                records_to_pay = salary_records

            paid_count = 0
            today = timezone.localdate()
            try:
                with transaction.atomic():
                    for salary in records_to_pay:
                        payable_date = salary_payable_date(salary.period_month)
                        if today < payable_date:
                            raise ValidationError(
                                f"{salary.employee.display_name} salary for {salary.period_month} can only be paid on "
                                f"{payable_date.strftime('%Y-%m-%d')} or later."
                            )
                        post_salary_accrual(salary, created_by=request.user)
                        salary.status = SalaryPayment.Status.PAID
                        salary.recorded_by = request.user
                        salary.payment_date = today
                        salary.save(update_fields=["status", "recorded_by", "payment_date"])
                        post_salary_payment(salary, created_by=request.user)
                        paid_count += 1
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
                return redirect("pending_salaries", period_month=period_month)

            if paid_count:
                messages.success(request, f"{paid_count} salary record(s) marked as paid.")
            else:
                messages.info(request, "No pending salary records were available to pay.")
            return redirect("pending_salaries", period_month=period_month)

        employee_id = request.POST.get("employee", "").strip()
        amount_str = request.POST.get("amount", "0").strip()
        status = request.POST.get("status", SalaryPayment.Status.PENDING)

        errors = []
        employee = employees.filter(pk=employee_id).first() if employee_id else None
        amount = None
        if not employee:
            errors.append("Please select a valid employee.")
        try:
            amount = Decimal(amount_str)
            if amount < 0:
                errors.append("Amount must be positive.")
        except (InvalidOperation, ValueError):
            errors.append("Invalid amount.")
        if status not in {SalaryPayment.Status.PAID, SalaryPayment.Status.PENDING}:
            errors.append("Invalid status.")

        if not errors and employee and amount is not None:
            breakdown = calculate_salary_breakdown(amount, Decimal("0.00"), bonuses=Decimal("0.00"))
            salary, _created = SalaryPayment.objects.update_or_create(
                employee=employee,
                period_month=period_month,
                defaults={
                    "amount": breakdown["net_pay"],
                    "gross_salary": breakdown["gross_salary"],
                    "paye_tax": breakdown["paye_tax"],
                    "nssf_employee": breakdown["nssf_employee"],
                    "nssf_employer": breakdown["nssf_employer"],
                    "bonus_amount": breakdown["bonus_amount"],
                    "net_pay": breakdown["net_pay"],
                    "payment_date": period_end(period_month),
                    "status": status,
                    "recorded_by": request.user,
                },
            )
            messages.success(request, "Salary record saved.")
            return _redirect_for_salary(salary)

        for error in errors:
            messages.error(request, error)

    page_obj, querystring = paginate(request, salary_records, per_page=20)
    context = {
        "employees": employees,
        "salary_records": page_obj,
        "page_obj": page_obj,
        "querystring": querystring,
        "period_month": period_month,
        "current_period_month": current_period_month,
        "hide_current_pending": hide_current_pending,
        "pending_visible_from": pending_visible_from,
    }
    return render(request, "payroll/salaries.html", context)


@login_required(login_url="login")
def salary_history(request):
    denied = _manager_only(request)
    if denied:
        return denied

    salary_records = SalaryPayment.objects.select_related("employee", "recorded_by").order_by(
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

    if request.method == "POST":
        gross_raw = request.POST.get("gross_salary", "0").strip()
        advances_raw = request.POST.get("advances_deducted", "0").strip()
        bonus_raw = request.POST.get("bonus_amount", "0").strip()
        net_raw = request.POST.get("net_pay", "0").strip()
        calculate_from = request.POST.get("calculate_from", "gross").strip()
        payment_date_raw = request.POST.get("payment_date", "").strip()
        status = request.POST.get("status", SalaryPayment.Status.PENDING).strip()
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

        payment_date = None
        if payment_date_raw:
            try:
                payment_date = date.fromisoformat(payment_date_raw)
            except ValueError:
                errors.append("Please enter a valid payment date.")

        if status not in {SalaryPayment.Status.PAID, SalaryPayment.Status.PENDING}:
            errors.append("Invalid status.")
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
            gross_salary = values["gross_salary"]
            if calculate_from == "net":
                gross_salary = calculate_gross_from_net(
                    values["net_pay"],
                    values["advances_deducted"],
                    bonuses=values["bonus_amount"],
                    pay_nssf=pay_nssf,
                    pay_paye=pay_paye,
                )
            breakdown = calculate_salary_breakdown(
                gross_salary,
                values["advances_deducted"],
                bonuses=values["bonus_amount"],
                pay_nssf=pay_nssf,
                pay_paye=pay_paye,
            )

            salary.gross_salary = breakdown["gross_salary"]
            salary.paye_tax = breakdown["paye_tax"]
            salary.nssf_employee = breakdown["nssf_employee"]
            salary.nssf_employer = breakdown["nssf_employer"]
            salary.advances_deducted = breakdown["advances_deducted"]
            salary.bonus_amount = breakdown["bonus_amount"]
            salary.net_pay = breakdown["net_pay"]
            salary.amount = breakdown["net_pay"]
            salary.payment_date = payment_date
            salary.status = status
            salary.recorded_by = request.user
            salary.edit_reason = edit_reason
            salary.save(
                update_fields=[
                    "gross_salary",
                    "paye_tax",
                    "nssf_employee",
                    "nssf_employer",
                    "advances_deducted",
                    "bonus_amount",
                    "net_pay",
                    "amount",
                    "payment_date",
                    "status",
                    "recorded_by",
                    "edit_reason",
                    "updated_at",
                ]
            )
            SalaryPaymentEditLog.objects.create(salary=salary, edited_by=request.user, reason=edit_reason)
            messages.success(request, f"Salary updated for {salary.employee.display_name}.")
            if salary.status == SalaryPayment.Status.PENDING:
                return redirect("pending_salaries", period_month=salary.period_month)
            return redirect("salary_history")

    return render(
        request,
        "payroll/edit_salary.html",
        {
            "salary": salary,
            "status_choices": SalaryPayment.Status.choices,
        },
    )
