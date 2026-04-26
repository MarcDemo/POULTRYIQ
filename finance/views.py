from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.shortcuts import redirect, render

from .models import ExpenseCategory, ExpenseTransaction, SalaryPayment

User = get_user_model()


def expense_form(request):
    categories = ExpenseCategory.objects.filter(is_active=True).order_by("name")
    today = date.today()

    if request.method == "POST":
        date_str = request.POST.get("date", "").strip()
        category_id = request.POST.get("category", "").strip()
        description = request.POST.get("description", "").strip()
        amount_str = request.POST.get("amount", "0").strip()
        payment_method = request.POST.get("payment_method", ExpenseTransaction.PAYMENT_CASH)

        errors = []
        expense_date = None
        total_amount = None
        category = None

        if not date_str:
            errors.append("Date is required.")
        else:
            try:
                expense_date = date.fromisoformat(date_str)
            except ValueError:
                errors.append("Invalid date.")

        if not category_id:
            errors.append("Category is required.")
        else:
            category = ExpenseCategory.objects.filter(pk=category_id, is_active=True).first()
            if not category:
                errors.append("Invalid category selected.")

        if not description:
            errors.append("Description is required.")

        try:
            total_amount = Decimal(amount_str)
            if total_amount < 0:
                errors.append("Amount must be positive.")
        except InvalidOperation:
            errors.append("Invalid amount.")

        if not errors and expense_date and total_amount is not None and category:
            ExpenseTransaction.objects.create(
                expense_date=expense_date,
                category=category,
                description=description,
                total_amount=total_amount,
                payment_method=payment_method,
                period_year=expense_date.year,
                period_month=expense_date.month,
                status=ExpenseTransaction.Status.DRAFT,
                created_by=request.user,
            )
            messages.success(request, "Expense recorded successfully.")
            return redirect("expense_form")

        for error in errors:
            messages.error(request, error)

    today_expenses = (
        ExpenseTransaction.objects
        .filter(expense_date=today)
        .select_related("category", "created_by")
        .order_by("-expense_id")
    )
    today_total = today_expenses.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")

    this_month_qs = ExpenseTransaction.objects.filter(
        period_year=today.year, period_month=today.month
    )
    this_month_total = this_month_qs.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")

    feed_total = this_month_qs.filter(
        category__code="FEED"
    ).aggregate(total=Sum("total_amount"))["total"] or Decimal("0")

    feed_percent = int(feed_total / this_month_total * 100) if this_month_total > 0 else 0

    context = {
        "categories": categories,
        "today_expenses": today_expenses,
        "today_total": today_total,
        "this_month_total": this_month_total,
        "feed_percent": feed_percent,
    }
    return render(request, "expense_form.html", context)


def expenses(request):
    all_expenses = (
        ExpenseTransaction.objects
        .select_related("category", "created_by", "approved_by")
        .order_by("-expense_date", "-expense_id")
    )
    total_amount = all_expenses.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")

    context = {
        "expenses": all_expenses,
        "total_amount": total_amount,
    }
    return render(request, "expenses.html", context)


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

    context = {
        "employees": employees,
        "salary_records": salary_records,
    }
    return render(request, "salaries.html", context)
