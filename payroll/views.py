from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.shortcuts import redirect, render

from .models import SalaryPayment
from poultryiq.pagination import paginate

User = get_user_model()


def salaries(request):
    employees = User.objects.filter(is_active=True).order_by("first_name", "username")
    salary_records = SalaryPayment.objects.select_related("employee", "recorded_by").order_by(
        "-period_month", "employee__username"
    )

    if request.method == "POST":
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
            employee = User.objects.filter(pk=employee_id, is_active=True).first()
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
            SalaryPayment.objects.create(
                employee=employee,
                period_month=period_month,
                amount=amount,
                status=status,
                recorded_by=request.user,
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
