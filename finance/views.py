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
    qs = (
        ExpenseTransaction.objects
        .select_related("category", "created_by", "approved_by")
        .order_by("-expense_date", "-expense_id")
    )

    f_date_from = request.GET.get("date_from", "").strip()
    f_date_to = request.GET.get("date_to", "").strip()
    f_category = request.GET.get("category", "").strip()
    f_status = request.GET.get("status", "").strip()
    f_payment_method = request.GET.get("payment_method", "").strip()

    if f_date_from:
        try:
            from datetime import date as _date
            qs = qs.filter(expense_date__gte=_date.fromisoformat(f_date_from))
        except ValueError:
            pass
    if f_date_to:
        try:
            from datetime import date as _date
            qs = qs.filter(expense_date__lte=_date.fromisoformat(f_date_to))
        except ValueError:
            pass
    if f_category:
        qs = qs.filter(category__pk=f_category)
    if f_status:
        qs = qs.filter(status=f_status)
    if f_payment_method:
        qs = qs.filter(payment_method=f_payment_method)

    total_amount = qs.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")
    categories = ExpenseCategory.objects.filter(is_active=True).order_by("name")

    context = {
        "expenses": qs,
        "total_amount": total_amount,
        "categories": categories,
        "payment_method_choices": ExpenseTransaction.PAYMENT_METHOD_CHOICES,
        "status_choices": ExpenseTransaction.Status.choices,
        "f_date_from": f_date_from,
        "f_date_to": f_date_to,
        "f_category": f_category,
        "f_status": f_status,
        "f_payment_method": f_payment_method,
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
