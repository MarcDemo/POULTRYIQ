from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import redirect, render

from .models import ExpenseCategory, ExpenseTransaction
from accounting.models import AccountType, AccountingCode, ChartOfAccount
from accounting.services import post_expense
from inventory.credit_alerts import create_credit_payment_alerts
from inventory.models import InventoryTransaction, Item, ItemCategory, Store, Supplier, SupplierProduct
from poultryiq.pagination import paginate

User = get_user_model()


DEFAULT_EXPENSE_CATEGORIES = [
    ("VET", "Veterinary & Medication", "COST_OF_REVENUE"),
    ("FEEDS", "Feeds", "COST_OF_REVENUE"),
    ("VACCINATION", "Vaccination", "COST_OF_REVENUE"),
    ("MEDICATION", "Medication", "COST_OF_REVENUE"),
    ("HUSKS", "Husks", "COST_OF_REVENUE"),
    ("OTHER_DIRECTCOSTS", "Other Direct Costs", "COST_OF_REVENUE"),
    ("SALARY", "Salary", "MONTHLY_EXPENSES"),
    ("BONUSES", "Bonuses", "MONTHLY_EXPENSES"),
    ("AUDIT_FEES", "Audit Fees", "MONTHLY_EXPENSES"),
    ("STATIONERY", "Stationery", "MONTHLY_EXPENSES"),
    ("INTERNET_SUBSCRIPTION", "Internet Subscription", "MONTHLY_EXPENSES"),
    ("INSURANCE", "Insurance", "MONTHLY_EXPENSES"),
    ("BADDEBTS", "Bad Debts", "MONTHLY_EXPENSES"),
    ("ELECTRICITY", "Electricity", "MONTHLY_EXPENSES"),
    ("WATER", "Water Bills", "MONTHLY_EXPENSES"),
    ("RENT", "Rent", "MONTHLY_EXPENSES"),
    ("SECURITY", "Security", "MONTHLY_EXPENSES"),
    ("GENERAL_OFFICE", "General Office", "MONTHLY_EXPENSES"),
    ("TRAVEL", "Travel", "MONTHLY_EXPENSES"),
    ("OTHER_MONTHLY", "Other Monthly", "MONTHLY_EXPENSES"),
    ("LABOUR", "Labour & Wages", "MONTHLY_EXPENSES"),
    ("UTILITIES", "Utilities", "MONTHLY_EXPENSES"),
    ("TRANSPORT", "Transport & Logistics", "MONTHLY_EXPENSES"),
    ("MAINTENANCE", "Maintenance & Repairs", "MONTHLY_EXPENSES"),
    ("EQUIPMENT", "Equipment & Tools", "DEPRECIATION"),
    ("INVENTORY_PURCHASE", "Inventory Purchases", "COST_OF_REVENUE"),
    ("BATCH_PURCHASE", "Bird Batch Purchase", "COST_OF_REVENUE"),
    ("OTHER", "Other", "MONTHLY_EXPENSES"),
]

MANUAL_EXPENSE_HIDDEN_CATEGORY_CODES = ["INVENTORY_PURCHASE", "BATCH_PURCHASE"]
SALARY_CATEGORY_CODES = ["SALARY"]
OTHER_EXPENSE_CATEGORY_CODES = {"OTHER", "LOSS_ON_DISPOSAL", "DONATIONS"}
PREPAYMENT_CATEGORY_CODES = {"ELECTRICITY", "WATER", "RENT", "UTILITIES"}


def _prepayment_type_for_expense(category, description):
    raw = f"{getattr(category, 'code', '')} {getattr(category, 'name', '')} {description or ''}".lower()
    if "rent" in raw:
        return ExpenseTransaction.PrepaymentType.RENT
    if "electric" in raw or "umeme" in raw or "power" in raw:
        return ExpenseTransaction.PrepaymentType.ELECTRICITY
    if "water" in raw:
        return ExpenseTransaction.PrepaymentType.WATER
    return ""


def _normalise_label(value):
    return (value or "").strip().lower().replace("&", "and")


def _supplier_product_tokens(supplier):
    raw = _normalise_label(supplier.product)
    return [token.strip() for token in raw.replace("/", ",").replace(";", ",").split(",") if token.strip()]


def _supplier_supplies_item(supplier, item_name):
    tokens = _supplier_product_tokens(supplier)
    if not tokens:
        return False
    return _normalise_label(item_name) in tokens


def _supplier_matches_catalog_category(supplier, category):
    return any(_supplier_supplies_item(supplier, item["name"]) for item in category["items"])


def _expense_category_for_catalog_category(category_code, category_name):
    code = (category_code or "").upper()
    candidates_by_code = {
        "FEED": ["FEEDS"],
        "DRUG": ["MEDICATION", "VACCINATION", "VET"],
        "BEDDING": ["HUSKS", "OTHER_DIRECTCOSTS"],
        "CONSUMABLE": ["OTHER_DIRECTCOSTS"],
        "EQUIPMENT": ["OTHER_DIRECTCOSTS"],
        "BIRDS": ["BATCH_PURCHASE", "OTHER_DIRECTCOSTS"],
    }

    for expense_code in candidates_by_code.get(code, []):
        match = ExpenseCategory.objects.filter(
            code=expense_code,
            expense_type=ExpenseCategory.ExpenseType.COST_OF_REVENUE,
            is_active=True,
        ).first()
        if match:
            return match

    return (
        ExpenseCategory.objects.filter(
            expense_type=ExpenseCategory.ExpenseType.COST_OF_REVENUE,
            is_active=True,
            name__icontains=category_name,
        ).first()
        or ExpenseCategory.objects.filter(
            expense_type=ExpenseCategory.ExpenseType.COST_OF_REVENUE,
            is_active=True,
            code__icontains=code,
        ).first()
        or ExpenseCategory.objects.filter(
            code="OTHER_DIRECTCOSTS",
            expense_type=ExpenseCategory.ExpenseType.COST_OF_REVENUE,
            is_active=True,
        ).first()
        or ExpenseCategory.objects.filter(
            expense_type=ExpenseCategory.ExpenseType.COST_OF_REVENUE,
            is_active=True,
        ).order_by("name").first()
    )


def _expense_category_for_account(account):
    expense_type = (
        ExpenseCategory.ExpenseType.COST_OF_REVENUE
        if account.account_type.legacy_code == "COST_OF_REVENUE"
        else ExpenseCategory.ExpenseType.MONTHLY_EXPENSES
    )
    category, _ = ExpenseCategory.objects.get_or_create(
        code=f"ACCT_{account.pk}",
        defaults={
            "name": account.account_name,
            "expense_type": expense_type,
            "account": account,
            "is_active": True,
        },
    )
    updates = []
    if category.account_id != account.pk:
        category.account = account
        updates.append("account")
    if category.expense_type != expense_type:
        category.expense_type = expense_type
        updates.append("expense_type")
    if not category.is_active:
        category.is_active = True
        updates.append("is_active")
    if updates:
        category.save(update_fields=updates)
    return category


def _get_or_create_inventory_item(catalog_item):
    category, _ = ItemCategory.objects.get_or_create(
        code=catalog_item["category_code"],
        defaults={"name": catalog_item["category_name"]},
    )
    item, created = Item.objects.get_or_create(
        name=catalog_item["name"],
        defaults={
            "category": category,
            "unit": catalog_item["unit"],
            "is_active": True,
        },
    )
    updates = []
    if item.category_id != category.pk:
        item.category = category
        updates.append("category")
    if item.unit != catalog_item["unit"]:
        item.unit = catalog_item["unit"]
        updates.append("unit")
    if not item.is_active:
        item.is_active = True
        updates.append("is_active")
    if updates:
        item.save(update_fields=updates)
    return item


def ensure_default_expense_categories():
    for code, name, expense_type in DEFAULT_EXPENSE_CATEGORIES:
        ExpenseCategory.objects.get_or_create(
            code=code,
            defaults={"name": name, "expense_type": expense_type, "is_active": True},
        )

    # Feed and bedding now flow through inventory purchases, not direct expense categories.
    ExpenseCategory.objects.filter(code__in=["FEED", "BEDDING"]).update(is_active=False)


def _parse_paid_upfront(value):
    try:
        paid_upfront = Decimal((value or "").strip())
    except (InvalidOperation, ValueError):
        return None, "Please enter a valid paid upfront percentage."
    if paid_upfront < 0 or paid_upfront > 100:
        return None, "Paid upfront percentage must be between 0 and 100."
    return paid_upfront, ""


def _parse_grace_period_days(value):
    try:
        grace_period_days = int((value or "").strip())
    except (TypeError, ValueError):
        return None, "Please enter a valid grace period in days."
    if grace_period_days < 0:
        return None, "Grace period cannot be negative."
    return grace_period_days, ""


def expense_form(request):
    ensure_default_expense_categories()
    expense_categories = ExpenseCategory.objects.filter(
        is_active=True,
        expense_type__in=[
            ExpenseCategory.ExpenseType.MONTHLY_EXPENSES,
        ],
    ).exclude(code__in=SALARY_CATEGORY_CODES).order_by("name")
    purchase_categories = ExpenseCategory.objects.filter(
        is_active=True,
        expense_type=ExpenseCategory.ExpenseType.COST_OF_REVENUE,
    ).order_by("name")
    expense_accounts = ChartOfAccount.objects.select_related("account_type").filter(
        is_active=True,
        account_type__account_nature=AccountType.AccountNature.EXPENSE,
    ).order_by("account_type__name", "account_name")
    suppliers = Supplier.objects.filter(is_active=True).order_by("name")
    supplier_payment_terms = {
        str(supplier.pk): {
            "payment_method": supplier.preferred_payment_method,
            "bank_account_number": supplier.bank_account_number,
            "momo_receiving_number": supplier.momo_receiving_number,
            "credit_repayment_plan": supplier.credit_repayment_plan,
            "credit_paid_upfront": str(supplier.credit_paid_upfront) if supplier.credit_paid_upfront is not None else "",
            "credit_grace_period_days": supplier.credit_grace_period_days,
            "credit_period": supplier.credit_period,
        }
        for supplier in suppliers
    }
    supplier_account_map = {
        str(supplier.pk): [
            {
                "id": product.pk,
                "name": product.name,
                "unit": product.unit,
                "account": product.account.account_name,
                "code": product.account.code,
            }
            for product in supplier.supplied_products.select_related("account").filter(is_active=True).order_by("account__account_name", "name")
        ]
        for supplier in suppliers.prefetch_related("supplied_products", "supplied_products__account")
    }
    today = date.today()
    active_tab = request.GET.get("tab", "expenses")

    if request.method == "POST":
        transaction_type = request.POST.get("type", "expense").strip().lower()
        active_tab = "purchases" if transaction_type == "purchase" else "expenses"
        date_str = request.POST.get("date", "").strip()
        category_id = request.POST.get("category", "").strip()
        account_id = request.POST.get("account", "").strip()
        supplier_product_id = request.POST.get("supplier_product", "").strip()
        description = request.POST.get("description", "").strip()
        other_category_detail = request.POST.get("other_category_detail", "").strip()
        amount_str = request.POST.get("amount", "0").strip()
        payment_method = request.POST.get("payment_method", ExpenseTransaction.PAYMENT_CASH)
        bank_account_number = request.POST.get("bank_account_number", "").strip()
        momo_receiving_number = request.POST.get("momo_receiving_number", "").strip()
        credit_repayment_plan = request.POST.get("credit_repayment_plan", "").strip()
        credit_paid_upfront_raw = request.POST.get("credit_paid_upfront", "").strip()
        credit_grace_period_days_raw = request.POST.get("credit_grace_period_days", "").strip()
        credit_period = request.POST.get("credit_period", "").strip()
        prepayment_start_raw = request.POST.get("prepayment_start_date", "").strip()
        prepayment_end_raw = request.POST.get("prepayment_end_date", "").strip()
        supplier_id = request.POST.get("supplier", "").strip()
        item_name = ""
        unit = request.POST.get("unit", "unit").strip() or "unit"
        quantity_raw = request.POST.get("quantity", "0").strip()
        unit_price_raw = request.POST.get("unit_price", "0").strip()

        errors = []
        expense_date = None
        total_amount = None
        category = None
        selected_account = None
        selected_supplier_product = None
        supplier = None
        inventory_item = None
        quantity = None
        unit_price = None
        credit_paid_upfront = None
        credit_grace_period_days = None
        credit_due_date = None
        prepayment_type = ""
        prepayment_start_date = None
        prepayment_end_date = None
        valid_payment_methods = {value for value, _label in ExpenseTransaction.PAYMENT_METHOD_CHOICES}

        if transaction_type == "salary":
            messages.info(request, "Salaries are recorded in the Payroll module.")
            return redirect("salaries")

        if transaction_type not in {"expense", "purchase"}:
            errors.append("Invalid transaction type.")

        if not date_str:
            expense_date = date.today()
        else:
            try:
                expense_date = date.fromisoformat(date_str)
            except ValueError:
                errors.append("Invalid date.")

        if supplier_product_id:
            selected_supplier_product = (
                SupplierProduct.objects.select_related("account", "account__account_type")
                .filter(
                    pk=supplier_product_id,
                    is_active=True,
                    account__is_active=True,
                    account__account_type__account_nature=AccountType.AccountNature.EXPENSE,
                )
                .first()
            )
            if selected_supplier_product:
                selected_account = selected_supplier_product.account
                item_name = selected_supplier_product.name
                unit = selected_supplier_product.unit
            else:
                errors.append("Please select a valid supplier product.")
        elif account_id:
            selected_account = (
                ChartOfAccount.objects.select_related("account_type")
                .filter(
                    pk=account_id,
                    is_active=True,
                    account_type__account_nature=AccountType.AccountNature.EXPENSE,
                )
                .first()
            )
            if not selected_account:
                errors.append("Please select a valid expense or cost of revenue account.")

        if transaction_type == "expense" and not category_id and not selected_account:
            errors.append("Account is required.")
        elif transaction_type == "expense":
            if selected_account:
                category = _expense_category_for_account(selected_account)
            else:
                category = ExpenseCategory.objects.filter(
                    pk=category_id,
                    is_active=True,
                ).first()
                if not category:
                    errors.append("Invalid category selected.")
                elif category.expense_type == ExpenseCategory.ExpenseType.COST_OF_REVENUE:
                    errors.append("Please choose an expense account.")
                elif category.expense_type != ExpenseCategory.ExpenseType.MONTHLY_EXPENSES:
                    errors.append("Fixed assets must be recorded in the Fixed Assets page.")
                elif category.code in SALARY_CATEGORY_CODES:
                    errors.append("Salaries should be recorded in Payroll.")

        if not description:
            errors.append("Description is required.")

        if transaction_type == "purchase":
            if not supplier_id:
                errors.append("Supplier is required for purchases.")
            else:
                supplier = Supplier.objects.filter(pk=supplier_id, is_active=True).first()
                if not supplier:
                    errors.append("Invalid supplier selected.")

            if not payment_method and supplier:
                payment_method = supplier.preferred_payment_method
                bank_account_number = bank_account_number or supplier.bank_account_number
                momo_receiving_number = momo_receiving_number or supplier.momo_receiving_number
                credit_repayment_plan = credit_repayment_plan or supplier.credit_repayment_plan
                credit_paid_upfront_raw = credit_paid_upfront_raw or (
                    str(supplier.credit_paid_upfront) if supplier.credit_paid_upfront is not None else ""
                )
                credit_grace_period_days_raw = credit_grace_period_days_raw or str(supplier.credit_grace_period_days or "")
                credit_period = credit_period or supplier.credit_period

            if selected_account:
                if selected_supplier_product and supplier and not supplier.supplied_products.filter(pk=selected_supplier_product.pk).exists():
                    errors.append(f"{supplier.name} is not listed as a supplier for {selected_supplier_product.name}.")
                category = _expense_category_for_account(selected_account)
                item_name = item_name or selected_account.account_name
            elif transaction_type == "purchase":
                errors.append("Product/account is required for purchases.")

            try:
                quantity = Decimal(quantity_raw)
                if quantity <= 0:
                    errors.append("Quantity must be greater than zero.")
            except (InvalidOperation, ValueError):
                errors.append("Please enter a valid quantity.")

            try:
                unit_price = Decimal(unit_price_raw)
                if unit_price < 0:
                    errors.append("Unit price cannot be negative.")
            except (InvalidOperation, ValueError):
                errors.append("Please enter a valid unit price.")

            if quantity is not None and unit_price is not None:
                total_amount = (quantity * unit_price).quantize(Decimal("0.01"))

        if payment_method not in valid_payment_methods:
            errors.append("Please select a valid payment method.")
        elif transaction_type == "purchase" and payment_method == ExpenseTransaction.PAYMENT_BANK:
            if not bank_account_number:
                errors.append("Please enter the bank account number for this purchase.")
            momo_receiving_number = ""
            credit_repayment_plan = ""
            credit_paid_upfront = None
            credit_grace_period_days = None
            credit_period = ""
        elif transaction_type == "purchase" and payment_method == ExpenseTransaction.PAYMENT_MOBILE:
            if not momo_receiving_number:
                errors.append("Please enter the receiving number for this MoMo purchase.")
            bank_account_number = ""
            credit_repayment_plan = ""
            credit_paid_upfront = None
            credit_grace_period_days = None
            credit_period = ""
        elif transaction_type == "purchase" and payment_method == ExpenseTransaction.PAYMENT_CREDIT:
            credit_paid_upfront, percentage_error = _parse_paid_upfront(credit_paid_upfront_raw)
            credit_grace_period_days, grace_error = _parse_grace_period_days(credit_grace_period_days_raw)
            if not credit_repayment_plan:
                errors.append("Please enter the repayment plan for this credit purchase.")
            if percentage_error:
                errors.append(percentage_error)
            if grace_error:
                errors.append(grace_error)
            if not credit_period:
                errors.append("Please enter the credit period for this credit purchase.")
            bank_account_number = ""
            momo_receiving_number = ""
        elif transaction_type == "purchase":
            bank_account_number = ""
            momo_receiving_number = ""
            credit_repayment_plan = ""
            credit_paid_upfront = None
            credit_grace_period_days = None
            credit_period = ""

        if category and category.code == "OTHER" and not other_category_detail:
            errors.append("Please describe the other category.")

        if category and category.code == "OTHER" and other_category_detail:
            description = f"[Other: {other_category_detail}] {description}" if description else f"Other: {other_category_detail}"

        if transaction_type != "purchase":
            try:
                total_amount = Decimal(amount_str)
                if total_amount < 0:
                    errors.append("Amount must be positive.")
            except InvalidOperation:
                errors.append("Invalid amount.")

        if transaction_type == "expense" and category:
            prepayment_type = _prepayment_type_for_expense(category, description)
            if prepayment_type:
                try:
                    prepayment_start_date = date.fromisoformat(prepayment_start_raw)
                except ValueError:
                    errors.append("Please provide the prepayment start date.")
                try:
                    prepayment_end_date = date.fromisoformat(prepayment_end_raw)
                except ValueError:
                    errors.append("Please provide the prepayment end date.")
                if prepayment_start_date and prepayment_end_date and prepayment_end_date < prepayment_start_date:
                    errors.append("Prepayment end date cannot be before the start date.")

        if (
            transaction_type == "purchase"
            and payment_method == ExpenseTransaction.PAYMENT_CREDIT
            and expense_date
            and credit_grace_period_days is not None
        ):
            credit_due_date = expense_date + timedelta(days=credit_grace_period_days)

        if not errors and expense_date and total_amount is not None and category:
            if transaction_type == "purchase":
                supplier_label = supplier.name if supplier else ""
                description = f"Purchase: {item_name}. Supplier: {supplier_label}. {description}"
                if payment_method == ExpenseTransaction.PAYMENT_BANK:
                    description = f"{description} Bank account: {bank_account_number}."
                if payment_method == ExpenseTransaction.PAYMENT_MOBILE:
                    description = f"{description} MoMo number: {momo_receiving_number}."
                if payment_method == ExpenseTransaction.PAYMENT_CREDIT:
                    description = f"{description} Credit: {credit_paid_upfront}% paid upfront, balance due by {credit_due_date}."

            payment_notes = ""
            if transaction_type == "purchase" and payment_method == ExpenseTransaction.PAYMENT_BANK:
                payment_notes = f"Bank account number: {bank_account_number}"
            elif transaction_type == "purchase" and payment_method == ExpenseTransaction.PAYMENT_MOBILE:
                payment_notes = f"MoMo receiving number: {momo_receiving_number}"
            elif transaction_type == "purchase" and payment_method == ExpenseTransaction.PAYMENT_CREDIT:
                payment_notes = (
                    f"Credit repayment plan: {credit_repayment_plan}\n"
                    f"Paid upfront: {credit_paid_upfront}%\n"
                    f"Grace period: {credit_grace_period_days} days\n"
                    f"Credit due date: {credit_due_date}\n"
                    f"Credit period: {credit_period}"
                )

            posting_account = selected_account or category.account
            if not posting_account:
                messages.error(request, f"{category.name} is not mapped to an expense account. Please select a chart account.")
                return redirect("expenses")

            try:
                with transaction.atomic():
                    expense = ExpenseTransaction.objects.create(
                        expense_date=expense_date,
                        category=category,
                        account=posting_account,
                        description=description,
                        item_name=item_name if transaction_type == "purchase" else "",
                        quantity=quantity if transaction_type == "purchase" else None,
                        unit=unit if transaction_type == "purchase" else "",
                        unit_cost=unit_price if transaction_type == "purchase" else None,
                        supplier_name=supplier.name if supplier else "",
                        supplier_contact=supplier.phone if supplier else "",
                        total_amount=total_amount,
                        payment_method=payment_method,
                        is_prepayment=bool(prepayment_type),
                        prepayment_type=prepayment_type,
                        prepayment_start_date=prepayment_start_date,
                        prepayment_end_date=prepayment_end_date,
                        period_year=expense_date.year,
                        period_month=expense_date.month,
                        status=ExpenseTransaction.Status.DRAFT,
                        notes=payment_notes,
                        created_by=request.user,
                    )
            
            # Generate accounting code with Excel-aligned P&L account types.
                    if category.expense_type == ExpenseCategory.ExpenseType.COST_OF_REVENUE:
                        account_type = "COST_OF_REVENUE"
                        prefix = "CRO"
                    elif category.expense_type == ExpenseCategory.ExpenseType.DEPRECIATION:
                        account_type = "DEPRECIATION"
                        prefix = "DEP"
                    elif category.code in OTHER_EXPENSE_CATEGORY_CODES:
                        account_type = "OTHER_EXPENSES"
                        prefix = "OEX"
                    else:
                        account_type = "EXPENSES"
                        prefix = "EXP"

                    AccountingCode.create_for(
                        account=posting_account,
                        content_object=expense,
                        description=expense.description,
                    )
                    post_expense(expense, created_by=request.user)

                    if transaction_type == "purchase" and selected_account:
                        store, _ = Store.objects.get_or_create(
                            name="Main Store",
                            defaults={"location_note": "Primary farm store", "is_active": True},
                        )
                        item_category, _ = ItemCategory.objects.get_or_create(
                            code=f"ACCT_{selected_account.account_type_id}",
                            defaults={"name": selected_account.account_type.name},
                        )
                        inventory_item, _ = Item.objects.get_or_create(
                            name=selected_account.account_name,
                            defaults={"category": item_category, "unit": unit, "is_active": True},
                        )
                        if inventory_item.unit != unit:
                            inventory_item.unit = unit
                            inventory_item.save(update_fields=["unit"])
                        InventoryTransaction.objects.create(
                            tx_date=expense_date,
                            tx_type=InventoryTransaction.TxType.IN_,
                            store=store,
                            item=inventory_item,
                            quantity=quantity,
                            supplier_name=supplier.name if supplier else "",
                            unit_price=unit_price,
                            payment_method=payment_method,
                            bank_account_number=bank_account_number,
                            momo_receiving_number=momo_receiving_number,
                            credit_repayment_plan=credit_repayment_plan,
                            credit_paid_upfront=credit_paid_upfront,
                            credit_grace_period_days=credit_grace_period_days,
                            credit_period=credit_period,
                            credit_due_date=credit_due_date,
                            reference=f"EXP-{expense.expense_id}",
                            created_by=request.user,
                        )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
                return redirect("expenses")

                if payment_method == ExpenseTransaction.PAYMENT_CREDIT:
                    credit_amount_due = (total_amount * (Decimal("100") - credit_paid_upfront) / Decimal("100")).quantize(Decimal("0.01"))
                    create_credit_payment_alerts(
                        supplier_name=supplier.name if supplier else "",
                        item_name=item_name,
                        amount_due=credit_amount_due,
                        due_date=credit_due_date,
                        paid_upfront=credit_paid_upfront,
                        created_by=request.user,
                    )
            
            label = "Purchase" if transaction_type == "purchase" else "Expense"
            messages.success(request, f"{label} recorded successfully.")
            return redirect(f"{request.path}?tab={active_tab}")

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
        category__code="FEEDS"
    ).aggregate(total=Sum("total_amount"))["total"] or Decimal("0")

    feed_percent = int(feed_total / this_month_total * 100) if this_month_total > 0 else 0

    context = {
        "expense_categories": expense_categories,
        "expense_accounts": expense_accounts,
        "supplier_account_map": supplier_account_map,
        "supplier_payment_terms": supplier_payment_terms,
        "suppliers": suppliers,
        "active_tab": active_tab,
        "today_expenses": today_expenses,
        "today_total": today_total,
        "this_month_total": this_month_total,
        "feed_percent": feed_percent,
    }
    return render(request, "expenses/expense_form.html", context)


def expenses(request):
    ensure_default_expense_categories()
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
    categories = ExpenseCategory.objects.filter(is_active=True).exclude(
        code__in=MANUAL_EXPENSE_HIDDEN_CATEGORY_CODES
    ).order_by("name")
    page_obj, querystring = paginate(request, qs, per_page=20)

    context = {
        "expenses": page_obj,
        "page_obj": page_obj,
        "querystring": querystring,
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
    return render(request, "expenses/expenses.html", context)
