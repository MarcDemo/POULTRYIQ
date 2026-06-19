from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.shortcuts import redirect, render

from .models import ExpenseCategory, ExpenseTransaction
from accounting.models import AccountingCode
from inventory.models import InventoryTransaction, Item, ItemCategory, Store, Supplier
from inventory.purchase_catalog import PURCHASE_CATALOG, find_purchase_catalog_item
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


def expense_form(request):
    ensure_default_expense_categories()
    expense_categories = ExpenseCategory.objects.filter(
        is_active=True,
        expense_type__in=[
            ExpenseCategory.ExpenseType.MONTHLY_EXPENSES,
            ExpenseCategory.ExpenseType.DEPRECIATION,
        ],
    ).exclude(code__in=SALARY_CATEGORY_CODES).order_by("name")
    purchase_categories = ExpenseCategory.objects.filter(
        is_active=True,
        expense_type=ExpenseCategory.ExpenseType.COST_OF_REVENUE,
    ).order_by("name")
    suppliers = Supplier.objects.filter(is_active=True).order_by("name")
    supplier_category_map = {
        str(supplier.pk): [
            category["code"]
            for category in PURCHASE_CATALOG
            if _supplier_matches_catalog_category(supplier, category)
        ]
        for supplier in suppliers
    }
    items_by_category = {
        category["code"]: [
            {
                "id": item["name"],
                "name": item["name"],
                "unit": item["unit"],
            }
            for item in category["items"]
        ]
        for category in PURCHASE_CATALOG
    }
    today = date.today()
    active_tab = request.GET.get("tab", "expenses")

    if request.method == "POST":
        transaction_type = request.POST.get("type", "expense").strip().lower()
        active_tab = "purchases" if transaction_type == "purchase" else "expenses"
        date_str = request.POST.get("date", "").strip()
        category_id = request.POST.get("category", "").strip()
        description = request.POST.get("description", "").strip()
        other_category_detail = request.POST.get("other_category_detail", "").strip()
        amount_str = request.POST.get("amount", "0").strip()
        payment_method = request.POST.get("payment_method", ExpenseTransaction.PAYMENT_CASH)
        supplier_id = request.POST.get("supplier", "").strip()
        catalog_category_code = request.POST.get("inventory_category", "").strip()
        item_name = request.POST.get("item", "").strip()
        quantity_raw = request.POST.get("quantity", "0").strip()
        unit_price_raw = request.POST.get("unit_price", "0").strip()

        errors = []
        expense_date = None
        total_amount = None
        category = None
        supplier = None
        catalog_category = None
        catalog_item = None
        inventory_item = None
        quantity = None
        unit_price = None

        if transaction_type == "salary":
            messages.info(request, "Salaries are recorded in the Payroll module.")
            return redirect("salaries")

        if transaction_type not in {"expense", "purchase"}:
            errors.append("Invalid transaction type.")

        if not date_str:
            errors.append("Date is required.")
        else:
            try:
                expense_date = date.fromisoformat(date_str)
            except ValueError:
                errors.append("Invalid date.")

        if transaction_type == "expense" and not category_id:
            errors.append("Category is required.")
        elif transaction_type == "expense":
            category = ExpenseCategory.objects.filter(
                pk=category_id,
                is_active=True,
            ).first()
            if not category:
                errors.append("Invalid category selected.")
            elif transaction_type == "purchase" and category.expense_type != ExpenseCategory.ExpenseType.COST_OF_REVENUE:
                errors.append("Please choose a purchase category.")
            elif transaction_type == "expense" and category.expense_type == ExpenseCategory.ExpenseType.COST_OF_REVENUE:
                errors.append("Please choose an expense category.")
            elif transaction_type == "expense" and category.code in SALARY_CATEGORY_CODES:
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

            if not catalog_category_code:
                errors.append("Category is required for purchases.")
            else:
                catalog_category = next(
                    (row for row in PURCHASE_CATALOG if row["code"] == catalog_category_code),
                    None,
                )
                if not catalog_category:
                    errors.append("Invalid purchase category selected.")
                elif supplier and not _supplier_matches_catalog_category(supplier, catalog_category):
                    errors.append(f"{supplier.name} is not listed as a supplier for {catalog_category['name']}.")

            if not item_name:
                errors.append("Item is required for purchases.")
            else:
                catalog_item = find_purchase_catalog_item(item_name)
                if not catalog_item:
                    errors.append("Invalid item selected.")
                elif catalog_category and catalog_item["category_code"] != catalog_category["code"]:
                    errors.append("Please choose an item from the selected category.")
                elif supplier and not _supplier_supplies_item(supplier, catalog_item["name"]):
                    errors.append(f"{supplier.name} is not listed as a supplier for {catalog_item['name']}.")
                else:
                    item_name = catalog_item["name"]

            if catalog_category:
                category = _expense_category_for_catalog_category(catalog_category["code"], catalog_category["name"])
                if not category:
                    errors.append("No cost-of-revenue expense category is available for this purchase.")

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

        if not errors and expense_date and total_amount is not None and category:
            if transaction_type == "purchase":
                supplier_label = supplier.name if supplier else ""
                description = f"Purchase: {item_name}. Category: {catalog_category['name']}. Supplier: {supplier_label}. {description}"

            expense = ExpenseTransaction.objects.create(
                expense_date=expense_date,
                category=category,
                description=description,
                item_name=item_name if transaction_type == "purchase" else "",
                quantity=quantity if transaction_type == "purchase" else None,
                unit=catalog_item["unit"] if transaction_type == "purchase" and catalog_item else "",
                unit_cost=unit_price if transaction_type == "purchase" else None,
                supplier_name=supplier.name if supplier else "",
                supplier_contact=supplier.phone if supplier else "",
                total_amount=total_amount,
                payment_method=payment_method,
                period_year=expense_date.year,
                period_month=expense_date.month,
                status=ExpenseTransaction.Status.DRAFT,
                created_by=request.user,
            )
            
            # Generate accounting code for this expense
            expense_type_map = {
                'COST_OF_REVENUE': ('CRO', 'Cost of Revenue'),
                'DEPRECIATION': ('DEP', 'Depreciation'),
                'MONTHLY_EXPENSES': ('ME', 'Monthly Expenses'),
            }
            
            prefix, _ = expense_type_map.get(
                category.expense_type,
                ('ME', 'Monthly Expenses')
            )
            
            # Format account name as "expense_of_{category_name}"
            account_name = f"expense_of_{category.name.lower()}"
            
            AccountingCode.create_or_get_accounting_code(
                prefix=prefix,
                account_type=category.expense_type,
                account_name=account_name,
                content_object=expense,
            )

            if transaction_type == "purchase" and catalog_item:
                store, _ = Store.objects.get_or_create(
                    name="Main Store",
                    defaults={"location_note": "Primary farm store", "is_active": True},
                )
                inventory_item = _get_or_create_inventory_item(catalog_item)
                InventoryTransaction.objects.create(
                    tx_date=expense_date,
                    tx_type=InventoryTransaction.TxType.IN_,
                    store=store,
                    item=inventory_item,
                    quantity=quantity,
                    supplier_name=supplier.name if supplier else "",
                    unit_price=unit_price,
                    reference=f"EXP-{expense.expense_id}",
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
        "purchase_categories": purchase_categories,
        "inventory_categories": PURCHASE_CATALOG,
        "items_by_category": items_by_category,
        "supplier_category_map": supplier_category_map,
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
