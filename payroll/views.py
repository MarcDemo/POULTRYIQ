from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.views import get_post_login_redirect
from hr.models import StaffProfile, WelfareRequest
from .models import SalaryBonus, SalaryPayment, SalaryPaymentEditLog
from poultryiq.pagination import paginate

User = get_user_model()


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
    return (
        User.objects.select_related("role", "staff_profile")
        .filter(is_active=True, role__code__in=["WORKER", "SUPERVISOR", "MANAGER"])
        .exclude(role__code__in=["OWNER", "INVESTOR"])
        .exclude(role__name__icontains="investor")
        .order_by("first_name", "username")
    )


def _money(value):
    return (value or Decimal("0.00")).quantize(Decimal("0.01"))


def calculate_paye(monthly_taxable_pay):
    income = _money(monthly_taxable_pay)
    if income <= Decimal("235000"):
        return Decimal("0.00")
    if income <= Decimal("335000"):
        return _money((income - Decimal("235000")) * Decimal("0.10"))
    if income <= Decimal("410000"):
        return _money(Decimal("10000") + (income - Decimal("335000")) * Decimal("0.20"))

    paye = Decimal("25000") + (income - Decimal("410000")) * Decimal("0.30")
    if income > Decimal("10000000"):
        paye += (income - Decimal("10000000")) * Decimal("0.10")
    return _money(paye)


def calculate_salary_breakdown(gross_salary, advances, bonuses=Decimal("0.00"), pay_nssf=True, pay_paye=True):
    gross = _money(gross_salary)
    nssf_employee = _money(gross * Decimal("0.05")) if pay_nssf else Decimal("0.00")
    nssf_employer = _money(gross * Decimal("0.10")) if pay_nssf else Decimal("0.00")
    taxable_pay = max(gross - nssf_employee, Decimal("0.00"))
    paye_tax = calculate_paye(taxable_pay) if pay_paye else Decimal("0.00")
    advances = _money(advances)
    bonuses = _money(bonuses)
    net_pay = max(gross - nssf_employee - paye_tax - advances + bonuses, Decimal("0.00"))
    return {
        "gross_salary": gross,
        "nssf_employee": nssf_employee,
        "nssf_employer": nssf_employer,
        "paye_tax": paye_tax,
        "advances_deducted": advances,
        "bonus_amount": bonuses,
        "net_pay": _money(net_pay),
    }


def calculate_gross_from_net(target_net, advances, bonuses=Decimal("0.00"), pay_nssf=True, pay_paye=True):
    target = _money(target_net)
    advances = _money(advances)
    bonuses = _money(bonuses)
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

    return _money(high)


def _period_end(period_month):
    year, month = [int(part) for part in period_month.split("-")]
    return date(year, month, 28)


def _approved_advances_for_period(employee, period_month):
    end_date = _period_end(period_month)
    return WelfareRequest.objects.filter(
        worker=employee,
        request_type=WelfareRequest.RequestType.SALARY_ADVANCE,
        status=WelfareRequest.Status.MANAGER_APPROVED,
        salary_payment__isnull=True,
        manager_reviewed_at__date__lte=end_date,
    )


def _bonus_total_for_period(employee, period_month):
    return SalaryBonus.objects.filter(employee=employee, period_month=period_month).aggregate(total=Sum("amount"))[
        "total"
    ] or Decimal("0.00")


@login_required(login_url="login")
def salaries(request):
    denied = _manager_only(request)
    if denied:
        return denied

    employees = _staff_users()
    salary_records = SalaryPayment.objects.select_related("employee", "recorded_by").order_by(
        "-period_month", "employee__username"
    )

    if request.method == "POST":
        action = request.POST.get("action", "record").strip()
        if action == "generate_monthly":
            period_month = request.POST.get("batch_month", "").strip()
            if not period_month:
                messages.error(request, "Month is required.")
                return redirect("salaries")

            prepared = 0
            created = 0
            for employee in employees:
                profile = getattr(employee, "staff_profile", None)
                gross_salary = profile.monthly_salary if profile else Decimal("0.00")
                pay_nssf = profile.pay_nssf if profile else True
                pay_paye = profile.pay_paye if profile else True
                advance_qs = _approved_advances_for_period(employee, period_month)
                advances = advance_qs.aggregate(total=Sum("advance_amount"))["total"] or Decimal("0.00")
                bonuses = _bonus_total_for_period(employee, period_month)
                breakdown = calculate_salary_breakdown(
                    gross_salary,
                    advances,
                    bonuses=bonuses,
                    pay_nssf=pay_nssf,
                    pay_paye=pay_paye,
                )
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
                        "payment_date": _period_end(period_month),
                        "status": SalaryPayment.Status.PENDING,
                        "recorded_by": request.user,
                    },
                )
                advance_qs.update(salary_payment=salary)
                prepared += 1
                created += 1 if was_created else 0

            messages.success(request, f"Monthly payroll prepared. Records ready: {prepared}. New records: {created}.")
            return redirect("salaries")

        if action == "grant_bonus":
            employee_id = request.POST.get("bonus_employee", "").strip()
            period_month = request.POST.get("bonus_month", "").strip()
            amount_raw = request.POST.get("bonus_amount", "0").strip()
            reason = request.POST.get("bonus_reason", "").strip()
            employee = employees.filter(pk=employee_id).first() if employee_id else None
            errors = []
            amount = Decimal("0.00")

            if not employee:
                errors.append("Please select a valid employee for the bonus.")
            if not period_month:
                errors.append("Bonus month is required.")
            if not reason:
                errors.append("Bonus reason is required.")
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
                SalaryBonus.objects.create(
                    employee=employee,
                    period_month=period_month,
                    amount=amount,
                    reason=reason,
                    granted_by=request.user,
                )
                messages.success(request, f"Bonus granted to {employee.display_name}. Prepare payroll to include it.")
            return redirect("salaries")

        if action in {"pay_selected", "pay_all"}:
            if action == "pay_selected":
                selected_ids = request.POST.getlist("salary_ids")
                if not selected_ids:
                    messages.error(request, "Select at least one salary record to pay.")
                    return redirect("salaries")
                records_to_pay = salary_records.filter(pk__in=selected_ids, status=SalaryPayment.Status.PENDING)
            else:
                records_to_pay = salary_records.filter(status=SalaryPayment.Status.PENDING)

            paid_count = 0
            today = timezone.localdate()
            for salary in records_to_pay:
                salary.status = SalaryPayment.Status.PAID
                salary.recorded_by = request.user
                if not salary.payment_date:
                    salary.payment_date = today
                salary.save(update_fields=["status", "recorded_by", "payment_date"])
                paid_count += 1

            if paid_count:
                messages.success(request, f"{paid_count} salary record(s) marked as paid.")
            else:
                messages.info(request, "No pending salary records were available to pay.")
            return redirect("salaries")

        employee_id = request.POST.get("employee", "").strip()
        period_month = request.POST.get("month", "").strip()
        amount_str = request.POST.get("amount", "0").strip()
        status = request.POST.get("status", SalaryPayment.Status.PENDING)

        errors = []
        employee = None
        amount = None

        if not employee_id:
            errors.append("Please select an employee.")
        else:
            employee = employees.filter(pk=employee_id).first()
            if not employee:
                errors.append("Invalid employee selected.")

        if not period_month:
            errors.append("Month is required.")

        try:
            amount = Decimal(amount_str)
            if amount < 0:
                errors.append("Amount must be positive.")
        except InvalidOperation:
            errors.append("Invalid amount.")

        if status not in {SalaryPayment.Status.PAID, SalaryPayment.Status.PENDING}:
            errors.append("Invalid status.")

        if not errors and employee and amount is not None:
            breakdown = calculate_salary_breakdown(amount, Decimal("0.00"), bonuses=Decimal("0.00"))
            SalaryPayment.objects.update_or_create(
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
                    "payment_date": _period_end(period_month),
                    "status": status,
                    "recorded_by": request.user,
                },
            )
            messages.success(request, "Salary record saved.")
            return redirect("salaries")

        for error in errors:
            messages.error(request, error)

    page_obj, querystring = paginate(request, salary_records, per_page=20)

    context = {
        "employees": employees,
        "salary_records": page_obj,
        "page_obj": page_obj,
        "querystring": querystring,
    }
    return render(request, "payroll/salaries.html", context)


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
        values = {"gross_salary": Decimal("0.00"), "advances_deducted": Decimal("0.00"), "bonus_amount": Decimal("0.00"), "net_pay": Decimal("0.00")}
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
                values[field_name] = _money(value)
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
            return redirect("salaries")

    return render(
        request,
        "payroll/edit_salary.html",
        {
            "salary": salary,
            "status_choices": SalaryPayment.Status.choices,
        },
    )
