from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.contrib import messages
from django.conf import settings
from django.db import transaction
from django.db.models import Prefetch
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify

from accounts.views import get_post_login_redirect
from accounts.decorators import manager_required
from expenses.models import ExpenseCategory, ExpenseTransaction
from accounting.models import ChartOfAccount
from poultry.models import PoultryBatch
from poultryiq.pagination import paginate
from .credit_alerts import create_credit_payment_alerts
from .models import InventoryRequisition, InventoryTransaction, Item, ItemCategory, Store, Supplier, SupplierProduct


DEFAULT_ITEM_CATEGORIES = [
    ("FEED", "Feed"),
    ("DRUG", "Drugs & Vaccines"),
    ("BEDDING", "Bedding & Litter"),
    ("CONSUMABLE", "Consumables"),
    ("OTHER", "Other"),
]

FIXED_ASSET_INVENTORY_CATEGORY_CODES = {"EQUIPMENT"}


def supplier_visible_accounts():
    return ChartOfAccount.objects.select_related("account_type").filter(
        is_active=True,
        show_on_suppliers=True,
        account_type__is_active=True,
        account_type__show_on_suppliers=True,
    )


def supplier_visible_products():
    return SupplierProduct.objects.select_related("account", "account__account_type").filter(
        is_active=True,
        account__is_active=True,
        account__show_on_suppliers=True,
        account__account_type__is_active=True,
        account__account_type__show_on_suppliers=True,
    )


def _role_code(user) -> str:
    return (getattr(user.role, "code", "") or "").upper()


def _supervisor_only(request):
    if not request.user.is_authenticated:
        return redirect("login")
    if _role_code(request.user) != "SUPERVISOR":
        messages.error(request, "Access denied: Supervisors only.")
        return redirect(get_post_login_redirect(request.user))
    return None


def _manager_only(request):
    if not request.user.is_authenticated:
        return redirect("login")
    if _role_code(request.user) not in ("MANAGER", "OWNER"):
        messages.error(request, "Access denied: Managers only.")
        return redirect(get_post_login_redirect(request.user))
    return None


def _normalise_tin(tin):
    return re.sub(r"\s+", "", tin or "")


def _extract_taxpayer_name(payload):
    if isinstance(payload, dict):
        for key in (
            "taxpayerName",
            "taxpayer_name",
            "legalName",
            "legal_name",
            "businessName",
            "business_name",
            "supplierName",
            "name",
        ):
            value = payload.get(key)
            if value:
                return str(value).strip()
        for value in payload.values():
            name = _extract_taxpayer_name(value)
            if name:
                return name
    elif isinstance(payload, list):
        for item in payload:
            name = _extract_taxpayer_name(item)
            if name:
                return name
    return ""


def _payload_marks_invalid(payload):
    if not isinstance(payload, dict):
        return False
    for key in ("valid", "isValid", "exists", "registered"):
        if key in payload:
            return payload.get(key) is False
    status = str(payload.get("status") or payload.get("code") or "").lower()
    message = str(payload.get("message") or payload.get("error") or "").lower()
    return "invalid" in status or "not found" in message or "invalid" in message


def _build_custom_category_code(name: str) -> str:
    """Create a short unique code for a user-defined category."""
    base = slugify(name).upper().replace("-", "_")
    if not base:
        base = "CUSTOM"
    if not base.startswith("CUSTOM_"):
        base = f"CUSTOM_{base}"

    code = base[:30]
    if not ItemCategory.objects.filter(code=code).exists():
        return code

    suffix = 2
    while True:
        suffix_str = f"_{suffix}"
        trimmed = base[: max(1, 30 - len(suffix_str))]
        candidate = f"{trimmed}{suffix_str}"
        if not ItemCategory.objects.filter(code=candidate).exists():
            return candidate
        suffix += 1


def ensure_inventory_defaults():
    for code, name in DEFAULT_ITEM_CATEGORIES:
        ItemCategory.objects.get_or_create(code=code, defaults={"name": name})

    store, _ = Store.objects.get_or_create(
        name="Main Store",
        defaults={"location_note": "Primary farm store", "is_active": True},
    )

    ExpenseCategory.objects.get_or_create(
        code="INVENTORY_PURCHASE",
        defaults={"name": "Inventory Purchases", "is_active": True},
    )
    return store


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


def _stock_status(percent_left):
    if percent_left <= 25:
        return {
            "status_label": "Low",
            "status_class": "stock-low",
            "progress_class": "stock-progress-low",
            "badge_class": "stock-badge-low",
        }
    if percent_left <= 60:
        return {
            "status_label": "Moderate",
            "status_class": "stock-moderate",
            "progress_class": "stock-progress-moderate",
            "badge_class": "stock-badge-moderate",
        }
    return {
        "status_label": "Enough",
        "status_class": "stock-enough",
        "progress_class": "stock-progress-enough",
        "badge_class": "stock-badge-enough",
    }


def _build_inventory_rows(store):
    tx_qs = (
        InventoryTransaction.objects
        .filter(store=store)
        .select_related("item", "item__category")
        .order_by("item__name", "-tx_date", "-tx_id")
    )

    grouped = {}
    for tx in tx_qs:
        row = grouped.setdefault(
            tx.item_id,
            {
                "item_id": tx.item_id,
                "item": tx.item.name,
                "category": tx.item.category.name,
                "unit": tx.item.unit,
                "stock_in": Decimal("0.000"),
                "stock_out": Decimal("0.000"),
                "adjustments": Decimal("0.000"),
                "supplier_name": "",
                "unit_price": None,
                "expiry_date": None,
            },
        )

        if tx.tx_type == InventoryTransaction.TxType.IN_:
            row["stock_in"] += tx.quantity
            if not row["supplier_name"] and tx.supplier_name:
                row["supplier_name"] = tx.supplier_name
            if row["unit_price"] is None and tx.unit_price is not None:
                row["unit_price"] = tx.unit_price
            if row["expiry_date"] is None and tx.expiry_date:
                row["expiry_date"] = tx.expiry_date
        elif tx.tx_type == InventoryTransaction.TxType.OUT:
            row["stock_out"] += tx.quantity
        else:
            row["adjustments"] += tx.quantity

    inventory_rows = []
    for row in grouped.values():
        current_qty = row["stock_in"] - row["stock_out"] + row["adjustments"]
        current_qty = max(current_qty, Decimal("0.000"))

        baseline = row["stock_in"] if row["stock_in"] > 0 else Decimal("1.000")
        percent_left = int((current_qty / baseline) * 100)
        percent_left = max(0, min(percent_left, 100))

        row["current_qty"] = current_qty
        row["percent_left"] = percent_left
        row.update(_stock_status(percent_left))
        inventory_rows.append(row)

    inventory_rows.sort(key=lambda x: x["item"].lower())
    return inventory_rows


# Create your views here.
@manager_required
def tin_lookup(request):
    tin = _normalise_tin(request.GET.get("tin", ""))

    if not re.fullmatch(r"\d{10}", tin):
        return JsonResponse({
            "ok": True,
            "valid": False,
            "message": "Invalid TIN number. A Uganda TIN should be 10 digits.",
        })

    lookup_url = getattr(settings, "URA_TIN_LOOKUP_URL", "")
    if not lookup_url:
        return JsonResponse({
            "ok": False,
            "valid": None,
            "message": "TIN lookup service is not configured.",
        }, status=503)

    separator = "&" if "?" in lookup_url else "?"
    request_url = f"{lookup_url}{separator}{urlencode({'tin': tin})}"
    headers = {"Accept": "application/json"}
    token = getattr(settings, "URA_TIN_LOOKUP_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        external_request = Request(request_url, headers=headers)
        with urlopen(external_request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code in (400, 404):
            return JsonResponse({
                "ok": True,
                "valid": False,
                "message": "Invalid TIN number.",
            })
        return JsonResponse({
            "ok": False,
            "valid": None,
            "message": "TIN lookup service is unavailable.",
        }, status=502)
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return JsonResponse({
            "ok": False,
            "valid": None,
            "message": "TIN lookup service is unavailable.",
        }, status=502)

    taxpayer_name = _extract_taxpayer_name(payload)
    if taxpayer_name:
        return JsonResponse({
            "ok": True,
            "valid": True,
            "tin": tin,
            "supplier_name": taxpayer_name,
            "message": "TIN verified.",
        })

    if _payload_marks_invalid(payload):
        return JsonResponse({
            "ok": True,
            "valid": False,
            "message": "Invalid TIN number.",
        })

    return JsonResponse({
        "ok": False,
        "valid": None,
        "message": "TIN lookup did not return a supplier name.",
    }, status=502)


@manager_required
def suppliers(request):
    if request.method == "POST":
        action = request.POST.get("action", "save_supplier").strip()
        if action == "add_supplier_product":
            account_id = request.POST.get("product_account", "").strip()
            product_name = request.POST.get("product_name", "").strip()
            unit = request.POST.get("product_unit", "unit").strip() or "unit"
            account = ChartOfAccount.objects.filter(
                pk=account_id,
                is_active=True,
                show_on_suppliers=True,
                account_type__is_active=True,
                account_type__show_on_suppliers=True,
            ).first()
            if not account:
                messages.error(request, "Select a supplier-enabled chart account.")
            elif not product_name:
                messages.error(request, "Product name is required.")
            else:
                SupplierProduct.objects.get_or_create(
                    account=account,
                    name=product_name,
                    defaults={"unit": unit, "is_active": True},
                )
                messages.success(request, f"{product_name} added under {account.account_name}.")
            return redirect("suppliers")

        name = request.POST.get("name", "").strip()
        tin_number = request.POST.get("tin_number", "").strip()
        phone = request.POST.get("phone", "").strip()
        location = request.POST.get("location", "").strip()
        use_other_supplied_product = request.POST.get("use_other_supplied_product") == "on"
        other_product_account_id = request.POST.get("other_product_account", "").strip()
        other_product_name = request.POST.get("other_product_name", "").strip()
        other_product_unit = request.POST.get("other_product_unit", "unit").strip() or "unit"
        preferred_payment_method = request.POST.get(
            "preferred_payment_method",
            Supplier.PaymentMethod.CASH,
        ).strip()
        bank_account_number = request.POST.get("bank_account_number", "").strip()
        momo_receiving_number = request.POST.get("momo_receiving_number", "").strip()
        credit_repayment_plan = request.POST.get("credit_repayment_plan", "").strip()
        credit_paid_upfront_raw = request.POST.get("credit_paid_upfront", "").strip()
        credit_grace_period_days_raw = request.POST.get("credit_grace_period_days", "").strip()
        credit_period = request.POST.get("credit_period", "").strip()
        selected_product_ids = request.POST.getlist("supplied_products")
        supplied_products = list(
            supplier_visible_products().filter(
                pk__in=selected_product_ids,
            )
        )
        product_names = sorted(item.name for item in supplied_products)
        other_product_account = None
        if use_other_supplied_product:
            other_product_account = supplier_visible_accounts().filter(pk=other_product_account_id).first()
            if other_product_name:
                product_names.append(other_product_name)
        product = ", ".join(product_names)
        valid_payment_methods = {value for value, _label in Supplier.PaymentMethod.choices}
        credit_paid_upfront = None
        credit_grace_period_days = None
        percentage_error = ""
        grace_error = ""
        if preferred_payment_method == Supplier.PaymentMethod.CREDIT:
            credit_paid_upfront, percentage_error = _parse_paid_upfront(credit_paid_upfront_raw)
            credit_grace_period_days, grace_error = _parse_grace_period_days(credit_grace_period_days_raw)

        if not name:
            messages.error(request, "Supplier name is required.")
        elif tin_number and not re.fullmatch(r"\d{10}", _normalise_tin(tin_number)):
            messages.error(request, "Invalid TIN number. A Uganda TIN should be 10 digits.")
        elif not supplied_products and not use_other_supplied_product:
            messages.error(request, "Select at least one supplied product or choose Other.")
        elif use_other_supplied_product and not other_product_account:
            messages.error(request, "Select the category for the other supplied product.")
        elif use_other_supplied_product and not other_product_name:
            messages.error(request, "Specify the other supplied product.")
        elif use_other_supplied_product and SupplierProduct.objects.filter(name__iexact=other_product_name).exists():
            messages.error(request, "That product name already exists. Please select it from the list instead.")
        elif preferred_payment_method not in valid_payment_methods:
            messages.error(request, "Select a valid preferred payment method.")
        elif preferred_payment_method == Supplier.PaymentMethod.BANK and not bank_account_number:
            messages.error(request, "Enter the bank account number for bank payments.")
        elif preferred_payment_method == Supplier.PaymentMethod.MOBILE_MONEY and not momo_receiving_number:
            messages.error(request, "Enter the receiving number for MoMo payments.")
        elif preferred_payment_method == Supplier.PaymentMethod.CREDIT and not credit_repayment_plan:
            messages.error(request, "Enter the repayment plan for credit suppliers.")
        elif preferred_payment_method == Supplier.PaymentMethod.CREDIT and percentage_error:
            messages.error(request, percentage_error)
        elif preferred_payment_method == Supplier.PaymentMethod.CREDIT and grace_error:
            messages.error(request, grace_error)
        elif preferred_payment_method == Supplier.PaymentMethod.CREDIT and not credit_period:
            messages.error(request, "Enter the credit period for credit suppliers.")
        else:
            tin_number = _normalise_tin(tin_number)
            if preferred_payment_method != Supplier.PaymentMethod.BANK:
                bank_account_number = ""
            if preferred_payment_method != Supplier.PaymentMethod.MOBILE_MONEY:
                momo_receiving_number = ""
            if preferred_payment_method != Supplier.PaymentMethod.CREDIT:
                credit_repayment_plan = ""
                credit_paid_upfront = None
                credit_grace_period_days = None
                credit_period = ""
            with transaction.atomic():
                if use_other_supplied_product:
                    other_product = SupplierProduct.objects.create(
                        account=other_product_account,
                        name=other_product_name,
                        unit=other_product_unit,
                        is_active=True,
                    )
                    supplied_products.append(other_product)

                supplier, created = Supplier.objects.update_or_create(
                    name__iexact=name,
                    defaults={
                        "name": name,
                        "tin_number": tin_number,
                        "phone": phone,
                        "location": location,
                        "product": product,
                        "other_supplied_products": "",
                        "preferred_payment_method": preferred_payment_method,
                        "bank_account_number": bank_account_number,
                        "momo_receiving_number": momo_receiving_number,
                        "credit_repayment_plan": credit_repayment_plan,
                        "credit_paid_upfront": credit_paid_upfront,
                        "credit_grace_period_days": credit_grace_period_days,
                        "credit_period": credit_period,
                        "is_active": True,
                    },
                )
                supplier.supplied_products.set(supplied_products)
                supplier.supplied_accounts.set([item.account for item in supplied_products])
            if created:
                messages.success(request, "Supplier saved successfully.")
            else:
                messages.success(request, "Supplier details updated.")
            return redirect("suppliers")

    suppliers_qs = (
        Supplier.objects.filter(is_active=True)
        .prefetch_related(
            Prefetch(
                "supplied_products",
                queryset=supplier_visible_products().order_by("account__account_type__name", "account__account_name", "name"),
                to_attr="visible_supplied_products",
            )
        )
        .order_by("name")
    )
    product_groups = []
    accounts = (
        supplier_visible_accounts()
        .order_by("account_type__name", "account_name")
    )
    for account in accounts:
        products = list(account.supplier_products.filter(is_active=True).order_by("name"))
        if products:
            product_groups.append({"account": account, "products": products})
    page_obj, querystring = paginate(request, suppliers_qs, per_page=20)
    return render(
        request,
        'suppliers.html',
        {
            "suppliers": page_obj,
            "page_obj": page_obj,
            "querystring": querystring,
            "product_groups": product_groups,
            "product_accounts": accounts,
        },
    )


@manager_required
def inventory_management(request):
    store = ensure_inventory_defaults()
    categories = ItemCategory.objects.exclude(
        code__in=FIXED_ASSET_INVENTORY_CATEGORY_CODES,
    ).order_by("name")
    supplier_list = (
        Supplier.objects.filter(is_active=True)
        .prefetch_related(
            Prefetch(
                "supplied_products",
                queryset=supplier_visible_products().order_by("account__account_type__name", "account__account_name", "name"),
                to_attr="visible_supplied_products",
            )
        )
        .order_by("name")
    )
    supplier_payment_terms = {
        supplier.name: {
            "payment_method": supplier.preferred_payment_method,
            "bank_account_number": supplier.bank_account_number,
            "momo_receiving_number": supplier.momo_receiving_number,
            "credit_repayment_plan": supplier.credit_repayment_plan,
            "credit_paid_upfront": str(supplier.credit_paid_upfront) if supplier.credit_paid_upfront is not None else "",
            "credit_grace_period_days": supplier.credit_grace_period_days,
            "credit_period": supplier.credit_period,
        }
        for supplier in supplier_list
    }
    supplier_product_choices = {
        supplier.name: [
            {
                "id": product.pk,
                "name": product.name,
                "unit": product.unit,
                "account": product.account_id,
                "account_label": f"{product.account.code} - {product.account.account_name}",
            }
            for product in supplier.visible_supplied_products
        ]
        for supplier in supplier_list
    }

    if request.method == "POST":
        item_name = request.POST.get("item", "").strip()
        supplier_product_id = request.POST.get("supplier_product", "").strip()
        other_item_name = request.POST.get("other_item_name", "").strip()
        category_id = request.POST.get("category", "").strip()
        other_category_name = request.POST.get("other_category_name", "").strip()
        quantity_raw = request.POST.get("quantity", "").strip()
        unit = request.POST.get("unit", "").strip() or "kg"
        supplier_name = request.POST.get("supplier_name", "").strip()
        unit_price_raw = request.POST.get("unit_price", "").strip()
        expiry_raw = request.POST.get("expiry_date", "").strip()
        payment_method = request.POST.get("payment_method", "").strip()
        bank_account_number = request.POST.get("bank_account_number", "").strip()
        momo_receiving_number = request.POST.get("momo_receiving_number", "").strip()
        credit_repayment_plan = request.POST.get("credit_repayment_plan", "").strip()
        credit_paid_upfront_raw = request.POST.get("credit_paid_upfront", "").strip()
        credit_grace_period_days_raw = request.POST.get("credit_grace_period_days", "").strip()
        credit_period = request.POST.get("credit_period", "").strip()

        errors = []
        category = ItemCategory.objects.filter(pk=category_id).first() if category_id else None
        final_category = category
        pending_custom_category_name = ""
        supplier = None
        selected_supplier_product = None
        quantity = None
        unit_price = None
        expiry_date = None
        credit_paid_upfront = None
        credit_grace_period_days = None
        credit_due_date = None
        valid_payment_methods = {value for value, _label in InventoryTransaction.PaymentMethod.choices}

        if not category:
            errors.append("Please select a valid category.")
        elif category.code in FIXED_ASSET_INVENTORY_CATEGORY_CODES:
            errors.append("Fixed assets must be recorded in the Fixed Assets page.")

        if supplier_name:
            supplier = Supplier.objects.filter(name__iexact=supplier_name, is_active=True).first()
            if supplier is None:
                errors.append("Please select a valid supplier from the list.")

        if supplier_product_id == "__other__":
            if not other_item_name:
                errors.append("Specify the product when Other is selected.")
            else:
                item_name = other_item_name
        elif supplier_product_id:
            selected_supplier_product = supplier_visible_products().filter(pk=supplier_product_id).first()
            if selected_supplier_product and supplier and not supplier.supplied_products.filter(pk=selected_supplier_product.pk).exists():
                selected_supplier_product = None
            if not selected_supplier_product:
                errors.append("Please select a valid product for this supplier.")
            else:
                item_name = selected_supplier_product.name
                unit = selected_supplier_product.unit or unit
        elif not item_name:
            errors.append("Select a supplied product or choose Other.")

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
        if not payment_method:
            payment_method = InventoryTransaction.PaymentMethod.CASH

        if payment_method not in valid_payment_methods:
            errors.append("Please select a valid payment method.")
        elif payment_method == InventoryTransaction.PaymentMethod.BANK:
            if not bank_account_number:
                errors.append("Please enter the bank account number for this purchase.")
            momo_receiving_number = ""
            credit_repayment_plan = ""
            credit_paid_upfront = None
            credit_grace_period_days = None
            credit_period = ""
        elif payment_method == InventoryTransaction.PaymentMethod.MOBILE_MONEY:
            if not momo_receiving_number:
                errors.append("Please enter the receiving number for this MoMo purchase.")
            bank_account_number = ""
            credit_repayment_plan = ""
            credit_paid_upfront = None
            credit_grace_period_days = None
            credit_period = ""
        elif payment_method == InventoryTransaction.PaymentMethod.CREDIT:
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
        else:
            bank_account_number = ""
            momo_receiving_number = ""
            credit_repayment_plan = ""
            credit_paid_upfront = None
            credit_grace_period_days = None
            credit_period = ""

        if category and category.code == "OTHER":
            if not other_category_name:
                errors.append("Please provide a category name when 'Other' is selected.")
            else:
                final_category = ItemCategory.objects.filter(name__iexact=other_category_name).first()
                if final_category is None:
                    pending_custom_category_name = other_category_name

        try:
            quantity = Decimal(quantity_raw)
            if quantity <= 0:
                errors.append("Quantity must be greater than zero.")
        except (InvalidOperation, ValueError):
            errors.append("Please enter a valid quantity.")

        try:
            unit_price = Decimal(unit_price_raw)
            if unit_price < 0:
                errors.append("Price cannot be negative.")
        except (InvalidOperation, ValueError):
            errors.append("Please enter a valid price.")

        if expiry_raw:
            try:
                expiry_date = date.fromisoformat(expiry_raw)
            except ValueError:
                errors.append("Please enter a valid expiry date.")

        if payment_method == InventoryTransaction.PaymentMethod.CREDIT and credit_grace_period_days is not None:
            credit_due_date = date.today() + timedelta(days=credit_grace_period_days)

        if not errors and final_category and quantity is not None and unit_price is not None:
            existing_item = Item.objects.filter(name__iexact=item_name).first()

            with transaction.atomic():
                if pending_custom_category_name and final_category is None:
                    final_category = ItemCategory.objects.create(
                        code=_build_custom_category_code(pending_custom_category_name),
                        name=pending_custom_category_name,
                    )

                if existing_item:
                    if existing_item.category_id != final_category.pk or existing_item.unit != unit:
                        existing_item.category = final_category
                        existing_item.unit = unit
                        existing_item.save(update_fields=["category", "unit"])
                    item = existing_item
                else:
                    item = Item.objects.create(name=item_name, category=final_category, unit=unit)

                tx = InventoryTransaction.objects.create(
                    tx_date=date.today(),
                    tx_type=InventoryTransaction.TxType.IN_,
                    store=store,
                    item=item,
                    quantity=quantity,
                    supplier_name=supplier_name,
                    unit_price=unit_price,
                    expiry_date=expiry_date,
                    payment_method=payment_method,
                    bank_account_number=bank_account_number,
                    momo_receiving_number=momo_receiving_number,
                    credit_repayment_plan=credit_repayment_plan,
                    credit_paid_upfront=credit_paid_upfront,
                    credit_grace_period_days=credit_grace_period_days,
                    credit_period=credit_period,
                    credit_due_date=credit_due_date,
                    created_by=request.user,
                )

                expense_category = ExpenseCategory.objects.get(code="INVENTORY_PURCHASE")
                total_amount = quantity * unit_price
                description = f"Inventory purchase: {item.name} ({quantity} {item.unit})"
                if supplier_name:
                    description = f"{description} - Supplier: {supplier_name}"
                if payment_method == InventoryTransaction.PaymentMethod.BANK:
                    description = f"{description} - Bank account: {bank_account_number}"
                if payment_method == InventoryTransaction.PaymentMethod.MOBILE_MONEY:
                    description = f"{description} - MoMo number: {momo_receiving_number}"
                if payment_method == InventoryTransaction.PaymentMethod.CREDIT:
                    description = f"{description} - Credit: {credit_paid_upfront}% paid upfront, balance due by {credit_due_date}"

                payment_notes = ""
                if payment_method == InventoryTransaction.PaymentMethod.BANK:
                    payment_notes = f"Bank account number: {bank_account_number}"
                elif payment_method == InventoryTransaction.PaymentMethod.MOBILE_MONEY:
                    payment_notes = f"MoMo receiving number: {momo_receiving_number}"
                elif payment_method == InventoryTransaction.PaymentMethod.CREDIT:
                    payment_notes = (
                        f"Credit repayment plan: {credit_repayment_plan}\n"
                        f"Paid upfront: {credit_paid_upfront}%\n"
                        f"Grace period: {credit_grace_period_days} days\n"
                        f"Credit due date: {credit_due_date}\n"
                        f"Credit period: {credit_period}"
                    )

                expense = ExpenseTransaction.objects.create(
                    expense_date=date.today(),
                    category=expense_category,
                    account=selected_supplier_product.account if selected_supplier_product else expense_category.account,
                    description=description,
                    item_name=item.name,
                    quantity=quantity,
                    unit=item.unit,
                    unit_cost=unit_price,
                    total_amount=total_amount,
                    supplier_name=supplier.name if supplier else supplier_name,
                    supplier_contact=supplier.phone if supplier else "",
                    payment_method=payment_method,
                    period_year=date.today().year,
                    period_month=date.today().month,
                    status=ExpenseTransaction.Status.DRAFT,
                    notes=payment_notes,
                    created_by=request.user,
                )

                tx.reference = f"EXP-{expense.expense_id}"
                tx.save(update_fields=["reference"])

                if payment_method == InventoryTransaction.PaymentMethod.CREDIT:
                    credit_amount_due = (total_amount * (Decimal("100") - credit_paid_upfront) / Decimal("100")).quantize(Decimal("0.01"))
                    create_credit_payment_alerts(
                        supplier_name=supplier_name,
                        item_name=item.name,
                        amount_due=credit_amount_due,
                        due_date=credit_due_date,
                        paid_upfront=credit_paid_upfront,
                        created_by=request.user,
                    )

            messages.success(request, "Inventory item saved and expense recorded.")
            return redirect("inventory_management")

        for error in errors:
            messages.error(request, error)

    inventory_rows = _build_inventory_rows(store)
    page_obj, querystring = paginate(request, inventory_rows, per_page=20)

    return render(
        request,
        'inventory_management.html',
        {
            'inventory': page_obj,
            'page_obj': page_obj,
            'querystring': querystring,
            'categories': categories,
            'suppliers': supplier_list,
            'payment_method_choices': InventoryTransaction.PaymentMethod.choices,
            'supplier_payment_terms': supplier_payment_terms,
            'supplier_product_choices': supplier_product_choices,
        },
    )


@login_required(login_url="login")
def supervisor_requisitions(request):
    denied = _supervisor_only(request)
    if denied:
        return denied

    items = Item.objects.filter(is_active=True).select_related("category").order_by("name")

    if request.method == "POST":
        item_id = request.POST.get("item", "").strip()
        item_name = request.POST.get("item_name", "").strip()
        quantity_raw = request.POST.get("quantity", "").strip()
        unit = request.POST.get("unit", "").strip() or "kg"
        needed_by_raw = request.POST.get("needed_by", "").strip()
        reason = request.POST.get("reason", "").strip()

        errors = []
        item = items.filter(pk=item_id).first() if item_id else None
        quantity = None
        needed_by = None

        if not item and not item_name:
            errors.append("Please select an item or type the item needed.")

        try:
            quantity = Decimal(quantity_raw)
            if quantity <= 0:
                errors.append("Quantity must be greater than zero.")
        except (InvalidOperation, ValueError):
            errors.append("Please enter a valid quantity.")

        if needed_by_raw:
            try:
                needed_by = date.fromisoformat(needed_by_raw)
            except ValueError:
                errors.append("Please enter a valid needed-by date.")

        if not reason:
            errors.append("Please explain why this item is needed.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            InventoryRequisition.objects.create(
                requested_by=request.user,
                item=item,
                item_name="" if item else item_name,
                quantity=quantity,
                unit=item.unit if item else unit,
                needed_by=needed_by,
                reason=reason,
            )
            messages.success(request, "Inventory requisition sent to the manager.")
            return redirect("supervisor_requisitions")

    requisitions = InventoryRequisition.objects.filter(requested_by=request.user).select_related(
        "item", "reviewed_by"
    )[:20]
    return render(
        request,
        "supervisor_requisitions.html",
        {
            "items": items,
            "requisitions": requisitions,
            "today": timezone.localdate(),
        },
    )


@login_required(login_url="login")
def manager_requisitions(request):
    denied = _manager_only(request)
    if denied:
        return denied

    queryset = InventoryRequisition.objects.select_related("requested_by", "item", "reviewed_by").filter(
        status=InventoryRequisition.Status.SUBMITTED
    )
    page_obj, querystring = paginate(request, queryset, per_page=20)
    return render(
        request,
        "manager_requisitions.html",
        {
            "requests": page_obj,
            "page_obj": page_obj,
            "querystring": querystring,
        },
    )


@login_required(login_url="login")
def manager_review_requisition(request, pk):
    denied = _manager_only(request)
    if denied:
        return denied
    if request.method != "POST":
        return redirect("manager_requisitions")

    requisition = get_object_or_404(
        InventoryRequisition.objects.filter(status=InventoryRequisition.Status.SUBMITTED),
        pk=pk,
    )
    action = request.POST.get("action", "").strip()
    notes = request.POST.get("notes", "").strip()
    if action == "approve":
        requisition.status = InventoryRequisition.Status.APPROVED
        message = "Inventory requisition approved."
    elif action == "reject":
        requisition.status = InventoryRequisition.Status.REJECTED
        message = "Inventory requisition rejected."
    else:
        messages.error(request, "Unknown requisition action.")
        return redirect("manager_requisitions")

    requisition.reviewed_by = request.user
    requisition.reviewed_at = timezone.now()
    requisition.review_notes = notes
    requisition.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes", "updated_at"])
    messages.success(request, message)
    return redirect("manager_requisitions")


@manager_required
def store_out(request):
    store = ensure_inventory_defaults()
    inventory_rows = _build_inventory_rows(store)
    available_rows = [row for row in inventory_rows if row["current_qty"] > 0]
    available_by_item_id = {str(row["item_id"]): row for row in available_rows}
    batches = PoultryBatch.objects.filter(status=PoultryBatch.Status.ACTIVE).order_by("batch_code")

    form_data = {
        "tx_date": date.today().isoformat(),
        "item_id": "",
        "quantity": "",
        "batch_id": "",
        "reference": "",
        "notes": "",
    }

    if request.method == "POST":
        form_data = {
            "tx_date": request.POST.get("tx_date", "").strip() or date.today().isoformat(),
            "item_id": request.POST.get("item", "").strip(),
            "quantity": request.POST.get("quantity", "").strip(),
            "batch_id": request.POST.get("batch", "").strip(),
            "reference": request.POST.get("reference", "").strip(),
            "notes": request.POST.get("notes", "").strip(),
        }

        errors = []
        tx_date = None
        quantity = None
        batch = None
        selected_row = available_by_item_id.get(form_data["item_id"])

        try:
            tx_date = date.fromisoformat(form_data["tx_date"])
        except ValueError:
            errors.append("Please enter a valid date.")

        if not selected_row:
            errors.append("Please select an item with stock available.")

        try:
            quantity = Decimal(form_data["quantity"])
            if quantity <= 0:
                errors.append("Quantity must be greater than zero.")
        except (InvalidOperation, ValueError):
            errors.append("Please enter a valid quantity.")

        if selected_row and quantity is not None and quantity > selected_row["current_qty"]:
            errors.append(
                f"Only {selected_row['current_qty']} {selected_row['unit']} is available for {selected_row['item']}."
            )

        if form_data["batch_id"]:
            batch = PoultryBatch.objects.filter(
                pk=form_data["batch_id"],
                status=PoultryBatch.Status.ACTIVE,
            ).first()
            if batch is None:
                errors.append("Please select a valid active batch.")

        if not errors and selected_row and tx_date and quantity is not None:
            item = Item.objects.get(pk=selected_row["item_id"])
            InventoryTransaction.objects.create(
                tx_date=tx_date,
                tx_type=InventoryTransaction.TxType.OUT,
                store=store,
                item=item,
                quantity=quantity,
                batch=batch,
                reference=form_data["reference"],
                notes=form_data["notes"],
                created_by=request.user,
            )
            messages.success(
                request,
                f"{quantity} {item.unit} of {item.name} recorded as taken out of store.",
            )
            return redirect("store")

        for error in errors:
            messages.error(request, error)

    stock_out_qs = (
        InventoryTransaction.objects
        .filter(store=store, tx_type=InventoryTransaction.TxType.OUT)
        .select_related("item", "batch", "created_by")
        .order_by("-tx_date", "-tx_id")
    )
    page_obj, querystring = paginate(request, stock_out_qs, per_page=15)

    return render(
        request,
        "store.html",
        {
            "available_rows": available_rows,
            "batches": batches,
            "form_data": form_data,
            "stock_out_records": page_obj,
            "page_obj": page_obj,
            "querystring": querystring,
        },
    )
