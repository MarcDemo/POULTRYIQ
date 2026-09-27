from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render

from inventory.models import InventoryTransaction
from poultry.models import ApprovalStatus, egg_collection
from poultryiq.pagination import paginate
from accounting.models import AccountingCode, ChartOfAccount
from accounting.services import account_by_system_code, payment_account_for_method, post_customer_payment, post_sale, post_sale_delivery
from accounts.decorators import sales_access_required
from .models import Customer, CustomerPayment, ReceivableEntry, ReceivableLedger, SaleInvoice, SaleItem


EGGS_PER_TRAY = Decimal("30")
WHT_RATE = Decimal("0.06")


PRODUCTS = {
    "eggs": {
        "label": "Eggs",
        "unit": "trays",
        "sold_filter": Q(product_name__iexact="Eggs"),
    },
    "damaged_eggs": {
        "label": "Damaged Eggs",
        "unit": "eggs",
        "sold_filter": Q(product_name__iexact="Damaged Eggs"),
    },
    "manure": {
        "label": "Manure",
        "unit": "kg",
        "inventory_filter": Q(item__category__code__iexact="MANURE"),
        "sold_filter": Q(product_name__iexact="Manure"),
    },
    "off_layers": {
        "label": "Off Layer Birds",
        "unit": "birds",
        "inventory_filter": Q(item__category__code__iexact="OFF_LAYER"),
        "sold_filter": Q(product_name__iexact="Off Layer Birds"),
    },
}

SALE_ACCOUNT_SYSTEM_CODES = {
    "eggs": "SALE_EGGS",
    "manure": "SALE_MANURE",
    "off_layers": "SALE_OFFLAYERS",
    "damaged_eggs": "SALE_DAMAGED_EGGS",
}


def _stock_from_inventory(inventory_filter):
    qs = InventoryTransaction.objects.filter(inventory_filter)
    qty_in = qs.filter(tx_type=InventoryTransaction.TxType.IN_).aggregate(
        total=Coalesce(Sum("quantity"), Value(Decimal("0.000")), output_field=DecimalField(max_digits=14, decimal_places=3))
    )["total"]
    qty_out = qs.filter(tx_type=InventoryTransaction.TxType.OUT).aggregate(
        total=Coalesce(Sum("quantity"), Value(Decimal("0.000")), output_field=DecimalField(max_digits=14, decimal_places=3))
    )["total"]
    qty_adjust = qs.filter(tx_type=InventoryTransaction.TxType.ADJUST).aggregate(
        total=Coalesce(Sum("quantity"), Value(Decimal("0.000")), output_field=DecimalField(max_digits=14, decimal_places=3))
    )["total"]
    return qty_in - qty_out + qty_adjust


def _eggs_collected_net():
    totals = egg_collection.objects.filter(status=ApprovalStatus.APPROVED).aggregate(
        eggs=Coalesce(Sum("eggs_collected"), Value(0)),
        broken=Coalesce(Sum("eggs_rejected"), Value(0)),
    )
    return Decimal(totals["eggs"] - totals["broken"])


def _damaged_eggs_collected():
    total = egg_collection.objects.filter(status=ApprovalStatus.APPROVED).aggregate(
        broken=Coalesce(Sum("eggs_rejected"), Value(0)),
    )["broken"]
    return Decimal(total or 0)


def _sold_qty(sold_filter):
    return SaleItem.objects.filter(sold_filter).aggregate(
        total=Coalesce(
            Sum("quantity"),
            Value(Decimal("0.000")),
            output_field=DecimalField(max_digits=14, decimal_places=3),
        )
    )["total"]


def _latest_unit_price(sold_filter):
    latest = SaleItem.objects.filter(sold_filter).order_by("-item_id").values_list("unit_price", flat=True).first()
    return latest or Decimal("0.00")


def _eggs_to_trays(eggs_qty):
    return (eggs_qty / EGGS_PER_TRAY).quantize(Decimal("0.001"))


def _format_trays_and_eggs(tray_qty):
    total_eggs = int((tray_qty * EGGS_PER_TRAY).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if total_eggs <= 0:
        return "0 trays"
    trays, eggs = divmod(total_eggs, int(EGGS_PER_TRAY))
    tray_label = "tray" if trays == 1 else "trays"
    if eggs:
        egg_label = "egg" if eggs == 1 else "eggs"
        return f"{trays} {tray_label} and {eggs} {egg_label}"
    return f"{trays} {tray_label}"


def _attach_order_item_displays(orders):
    for order in orders:
        for item in order.items.all():
            if (item.product_name or "").strip().lower() == "eggs" and (item.unit or "").strip().lower() == "trays":
                item.qty_display = _format_trays_and_eggs(item.quantity)
            else:
                item.qty_display = f"{item.quantity} {item.unit}"


def _is_booking_invoice(invoice):
    return "Sale type: Booking" in (invoice.notes or "")


def _refresh_invoice_financial_status(invoice, receivable):
    if invoice.status == SaleInvoice.Status.CANCELLED:
        return
    if receivable.balance <= 0:
        next_status = SaleInvoice.Status.PAID
    elif receivable.amount_paid > 0:
        next_status = SaleInvoice.Status.ISSUED
    elif _is_booking_invoice(invoice):
        next_status = SaleInvoice.Status.DRAFT
    else:
        next_status = SaleInvoice.Status.ISSUED
    if invoice.status != next_status:
        invoice.status = next_status
        invoice.save(update_fields=["status"])


def _sold_eggs_in_trays(sold_filter):
    sold_rows = SaleItem.objects.filter(sold_filter).values("unit").annotate(
        total=Coalesce(
            Sum("quantity"),
            Value(Decimal("0.000")),
            output_field=DecimalField(max_digits=14, decimal_places=3),
        )
    )
    eggs_total = Decimal("0.000")
    trays_total = Decimal("0.000")
    for row in sold_rows:
        if (row["unit"] or "").strip().lower() == "trays":
            trays_total += row["total"]
        else:
            eggs_total += row["total"]
    return trays_total + _eggs_to_trays(eggs_total)


def _latest_egg_price_per_tray(sold_filter):
    latest = SaleItem.objects.filter(sold_filter).order_by("-item_id").values("unit", "unit_price").first()
    if not latest:
        return Decimal("0.00")
    if (latest["unit"] or "").strip().lower() == "trays":
        return latest["unit_price"]
    return (latest["unit_price"] * EGGS_PER_TRAY).quantize(Decimal("0.01"))


def _next_invoice_no(invoice_date: date):
    prefix = f"INV-{invoice_date.year}-"
    count = SaleInvoice.objects.filter(invoice_no__startswith=prefix).count() + 1
    return f"{prefix}{count:04d}"


def _build_product_stock():
    stock = {}

    eggs_source = _eggs_to_trays(_eggs_collected_net())
    eggs_sold = _sold_eggs_in_trays(PRODUCTS["eggs"]["sold_filter"])
    eggs_available = max(eggs_source - eggs_sold, Decimal("0.000"))
    eggs_price = _latest_egg_price_per_tray(PRODUCTS["eggs"]["sold_filter"])
    stock["eggs"] = {
        "key": "eggs",
        "label": PRODUCTS["eggs"]["label"],
        "unit": PRODUCTS["eggs"]["unit"],
        "source_qty": eggs_source,
        "source_display": _format_trays_and_eggs(eggs_source),
        "sold_qty": eggs_sold,
        "sold_display": _format_trays_and_eggs(eggs_sold),
        "available_qty": eggs_available,
        "available_display": _format_trays_and_eggs(eggs_available),
        "suggested_price": eggs_price,
        "available_value": (eggs_available * eggs_price).quantize(Decimal("0.01")),
    }

    damaged_source = _damaged_eggs_collected()
    damaged_sold = _sold_qty(PRODUCTS["damaged_eggs"]["sold_filter"])
    damaged_available = max(damaged_source - damaged_sold, Decimal("0.000"))
    damaged_price = _latest_unit_price(PRODUCTS["damaged_eggs"]["sold_filter"])
    stock["damaged_eggs"] = {
        "key": "damaged_eggs",
        "label": PRODUCTS["damaged_eggs"]["label"],
        "unit": PRODUCTS["damaged_eggs"]["unit"],
        "source_qty": damaged_source,
        "sold_qty": damaged_sold,
        "available_qty": damaged_available,
        "suggested_price": damaged_price,
        "available_value": (damaged_available * damaged_price).quantize(Decimal("0.01")),
    }

    for key in ("manure", "off_layers"):
        cfg = PRODUCTS[key]
        source = _stock_from_inventory(cfg["inventory_filter"])
        sold = _sold_qty(cfg["sold_filter"])
        available = max(source - sold, Decimal("0.000"))
        unit_price = _latest_unit_price(cfg["sold_filter"])
        stock[key] = {
            "key": key,
            "label": cfg["label"],
            "unit": cfg["unit"],
            "source_qty": source,
            "sold_qty": sold,
            "available_qty": available,
            "suggested_price": unit_price,
            "available_value": (available * unit_price).quantize(Decimal("0.01")),
        }

    return stock


def _pending_orders_queryset():
    return (
        SaleInvoice.objects.filter(delivery_status=SaleInvoice.DeliveryStatus.PENDING)
        .exclude(status=SaleInvoice.Status.CANCELLED)
        .select_related("customer", "created_by")
        .prefetch_related("items", "receivable")
        .order_by("-invoice_date", "-invoice_id")
    )


def _pending_orders_metrics(queryset):
    pending_total_value = queryset.aggregate(
        total=Coalesce(
            Sum("total_amount"),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    )["total"]
    pending_outstanding_value = queryset.aggregate(
        total=Coalesce(
            Sum("receivable__balance"),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    )["total"]
    return {
        "pending_orders_count": queryset.count(),
        "pending_orders_total_value": pending_total_value,
        "pending_orders_outstanding_value": pending_outstanding_value,
        "pending_due_today_count": queryset.filter(due_date=date.today()).count(),
        "pending_overdue_count": queryset.filter(due_date__isnull=False, due_date__lt=date.today()).count(),
    }


def _payment_method_from_post(raw_value):
    return {
        "CASH": Customer.PaymentMethod.CASH,
        "MOBILE MONEY": Customer.PaymentMethod.MOMO,
        "MOMO": Customer.PaymentMethod.MOMO,
        "BANK": Customer.PaymentMethod.BANK,
        "CREDIT": Customer.PaymentMethod.CREDIT,
    }.get((raw_value or "").strip().upper(), Customer.PaymentMethod.CASH)


def _customer_from_sale_value(raw_value):
    """Accept current customer IDs while retaining name support for legacy form posts."""
    value = (raw_value or "").strip()
    if not value:
        return None
    if value.isdigit():
        return Customer.objects.filter(customer_id=value, is_active=True).first()
    return Customer.objects.filter(name__iexact=value, is_active=True).first()


def _customer_open_receivable_balance(customer):
    return ReceivableLedger.objects.filter(
        invoice__customer=customer,
        invoice__status__in=[SaleInvoice.Status.DRAFT, SaleInvoice.Status.ISSUED],
        balance__gt=0,
    ).aggregate(
        total=Coalesce(
            Sum("balance"),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    )["total"]


def _credit_validation_error(customer, new_credit_amount):
    """Return a customer-facing message when a new balance cannot be put on credit."""
    if new_credit_amount <= 0:
        return None
    if not customer.allow_credit:
        return f"{customer.name} is not approved for credit. Collect the full amount before saving the sale."

    # A zero limit has historically meant that no ceiling was configured. Keep that
    # behaviour for existing customers while enforcing every configured limit.
    if customer.credit_limit > 0:
        outstanding = _customer_open_receivable_balance(customer)
        proposed_balance = (outstanding + new_credit_amount).quantize(Decimal("0.01"))
        if proposed_balance > customer.credit_limit:
            return (
                f"This sale would put {customer.display_id} at UGX {proposed_balance:,.0f}, "
                f"above its UGX {customer.credit_limit:,.0f} credit limit."
            )
    return None


def _add_receivable_entry(*, receivable, entry_date, entry_type, amount, balance_after, reference="", notes="", created_by=None):
    return ReceivableEntry.objects.create(
        receivable=receivable,
        entry_date=entry_date,
        entry_type=entry_type,
        amount=amount,
        balance_after=balance_after,
        reference=reference,
        notes=notes,
        created_by=created_by,
    )


def _record_receivable_payment(*, invoice, amount, method, reference, notes, received_by, payment_date=None):
    """Create the payment, its accounting journal, and its immutable AR movement."""
    receivable = getattr(invoice, "receivable", None)
    if not receivable:
        raise ValidationError("No receivable record found for this invoice.")
    amount = Decimal(amount).quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValidationError("Payment amount must be greater than zero.")
    if amount > receivable.balance:
        raise ValidationError(
            f"Payment amount cannot exceed the outstanding balance of UGX {receivable.balance:,.0f}."
        )

    payment_date = payment_date or date.today()
    payment = CustomerPayment.objects.create(
        invoice=invoice,
        customer=invoice.customer,
        payment_date=payment_date,
        method=method,
        amount=amount,
        reference=reference,
        notes=notes,
        received_by=received_by,
    )
    post_customer_payment(payment, created_by=received_by)
    receivable.amount_paid = (receivable.amount_paid + amount).quantize(Decimal("0.01"))
    receivable.balance = (receivable.amount_due - receivable.amount_paid).quantize(Decimal("0.01"))
    receivable.last_payment_date = payment_date
    receivable.save(update_fields=["amount_paid", "balance", "last_payment_date"])
    _add_receivable_entry(
        receivable=receivable,
        entry_date=payment_date,
        entry_type=ReceivableEntry.EntryType.PAYMENT,
        amount=amount,
        balance_after=receivable.balance,
        reference=reference or f"PAY-{payment.payment_id}",
        notes=notes,
        created_by=received_by,
    )
    _refresh_invoice_financial_status(invoice, receivable)
    return payment, receivable


def _customer_form_data(request):
    return {
        "name": request.POST.get("name", "").strip(),
        "contact_person": request.POST.get("contact_person", "").strip(),
        "phone_number": request.POST.get("phone_number", "").strip(),
        "email": request.POST.get("email", "").strip(),
        "address": request.POST.get("address", "").strip(),
        "preferred_payment_method": _payment_method_from_post(request.POST.get("preferred_payment_method", "CASH")),
        "momo_receiving_number": request.POST.get("momo_receiving_number", "").strip(),
        "bank_account_number": request.POST.get("bank_account_number", "").strip(),
        "credit_repayment_plan": request.POST.get("credit_repayment_plan", "").strip(),
        "allow_credit": request.POST.get("allow_credit") == "on",
        "pay_wht": request.POST.get("pay_wht") == "on",
        "credit_limit": request.POST.get("credit_limit", "0").strip(),
        "credit_days": request.POST.get("credit_days", "0").strip(),
        "is_active": request.POST.get("is_active", "on") == "on",
    }


def _validate_customer_form(form_data, customer=None):
    errors = []
    credit_limit = Decimal("0.00")
    credit_days = 0

    if not form_data["name"]:
        errors.append("Customer name is required.")
    else:
        duplicate_qs = Customer.objects.filter(name__iexact=form_data["name"])
        if customer:
            duplicate_qs = duplicate_qs.exclude(pk=customer.pk)
        if duplicate_qs.exists():
            errors.append("A customer with this name already exists.")

    try:
        credit_limit = Decimal(form_data["credit_limit"] or "0")
        if credit_limit < 0:
            errors.append("Credit limit cannot be negative.")
    except (InvalidOperation, ValueError):
        errors.append("Please enter a valid credit limit.")

    try:
        credit_days = int(form_data["credit_days"] or "0")
        if credit_days < 0:
            errors.append("Credit days cannot be negative.")
    except ValueError:
        errors.append("Please enter valid credit days.")

    if form_data["preferred_payment_method"] == Customer.PaymentMethod.MOMO and not form_data["momo_receiving_number"]:
        errors.append("MoMo number is required for mobile money customers.")
    if form_data["preferred_payment_method"] == Customer.PaymentMethod.BANK and not form_data["bank_account_number"]:
        errors.append("Bank account number is required for bank customers.")
    if form_data["preferred_payment_method"] == Customer.PaymentMethod.CREDIT and not form_data["credit_repayment_plan"]:
        errors.append("Credit repayment plan is required for credit customers.")

    return errors, credit_limit, credit_days


def _save_customer_from_form(form_data, credit_limit, credit_days, customer=None):
    customer = customer or Customer()
    customer.name = form_data["name"]
    customer.contact_person = form_data["contact_person"]
    customer.phone_number = form_data["phone_number"]
    customer.email = form_data["email"]
    customer.address = form_data["address"]
    customer.preferred_payment_method = form_data["preferred_payment_method"]
    customer.momo_receiving_number = form_data["momo_receiving_number"]
    customer.bank_account_number = form_data["bank_account_number"]
    customer.credit_repayment_plan = form_data["credit_repayment_plan"]
    customer.allow_credit = form_data["allow_credit"]
    customer.pay_wht = form_data["pay_wht"]
    customer.credit_limit = credit_limit
    customer.credit_days = credit_days
    customer.is_active = form_data["is_active"]
    customer.save()
    return customer


@sales_access_required
def customers(request):
    customers_qs = Customer.objects.annotate(
        outstanding_balance=Coalesce(
            Sum("invoices__receivable__balance"),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    ).order_by("name")
    query = request.GET.get("q", "").strip()
    if query:
        search_filter = (
            Q(name__icontains=query)
            | Q(contact_person__icontains=query)
            | Q(phone_number__icontains=query)
            | Q(email__icontains=query)
        )
        if query.upper().startswith("CUST-"):
            query = query[5:]
        if query.isdigit():
            search_filter |= Q(customer_id=int(query))
        customers_qs = customers_qs.filter(search_filter)

    if request.method == "POST":
        form_data = _customer_form_data(request)
        customer_id = request.POST.get("customer_id", "").strip()
        customer = Customer.objects.filter(pk=customer_id).first() if customer_id else None
        errors, credit_limit, credit_days = _validate_customer_form(form_data, customer=customer)

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            customer = _save_customer_from_form(form_data, credit_limit, credit_days, customer=customer)
            messages.success(request, f"Customer profile saved for {customer.name}.")
            return redirect("customer_profile", pk=customer.pk)
    else:
        form_data = {
            "preferred_payment_method": Customer.PaymentMethod.CASH,
            "allow_credit": True,
            "pay_wht": False,
            "credit_limit": "0.00",
            "credit_days": "0",
            "is_active": True,
        }

    page_obj, querystring = paginate(request, customers_qs, per_page=20)
    return render(
        request,
        "customers.html",
        {
            "customers": page_obj,
            "page_obj": page_obj,
            "querystring": querystring,
            "q": query,
            "form_data": form_data,
            "payment_method_choices": Customer.PaymentMethod.choices,
        },
    )


@sales_access_required
def customer_profile(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == "POST":
        form_data = _customer_form_data(request)
        errors, credit_limit, credit_days = _validate_customer_form(form_data, customer=customer)
        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            customer = _save_customer_from_form(form_data, credit_limit, credit_days, customer=customer)
            messages.success(request, f"Customer profile updated for {customer.name}.")
            return redirect("customer_profile", pk=customer.pk)
    else:
        form_data = {
            "name": customer.name,
            "contact_person": customer.contact_person,
            "phone_number": customer.phone_number,
            "email": customer.email,
            "address": customer.address,
            "preferred_payment_method": customer.preferred_payment_method,
            "momo_receiving_number": customer.momo_receiving_number,
            "bank_account_number": customer.bank_account_number,
            "credit_repayment_plan": customer.credit_repayment_plan,
            "allow_credit": customer.allow_credit,
            "pay_wht": customer.pay_wht,
            "credit_limit": customer.credit_limit,
            "credit_days": customer.credit_days,
            "is_active": customer.is_active,
        }

    invoices = (
        customer.invoices.select_related("receivable", "created_by")
        .prefetch_related("items")
        .order_by("-invoice_date", "-invoice_id")[:20]
    )
    payments = customer.payments.select_related("invoice", "received_by").order_by("-payment_date", "-payment_id")[:20]
    receivable_entries = ReceivableEntry.objects.filter(
        receivable__invoice__customer=customer
    ).select_related("receivable", "receivable__invoice").order_by("-entry_date", "-receivable_entry_id")[:30]
    totals = customer.invoices.aggregate(
        sales=Coalesce(Sum("total_amount"), Value(Decimal("0.00")), output_field=DecimalField(max_digits=14, decimal_places=2)),
        paid=Coalesce(Sum("receivable__amount_paid"), Value(Decimal("0.00")), output_field=DecimalField(max_digits=14, decimal_places=2)),
        balance=Coalesce(Sum("receivable__balance"), Value(Decimal("0.00")), output_field=DecimalField(max_digits=14, decimal_places=2)),
    )
    return render(
        request,
        "customer_profile.html",
        {
            "customer": customer,
            "form_data": form_data,
            "payment_method_choices": Customer.PaymentMethod.choices,
            "invoices": invoices,
            "payments": payments,
            "receivable_entries": receivable_entries,
            "totals": totals,
        },
    )


@sales_access_required
def receivables(request):
    """A focused customer-credit register with balances and payment history links."""
    query = request.GET.get("q", "").strip()
    ledgers = ReceivableLedger.objects.select_related("invoice", "invoice__customer").filter(
        balance__gt=0,
        invoice__status__in=[SaleInvoice.Status.DRAFT, SaleInvoice.Status.ISSUED],
    ).order_by("invoice__due_date", "invoice__invoice_date", "invoice__invoice_id")
    if query:
        search_filter = Q(invoice__invoice_no__icontains=query) | Q(invoice__customer__name__icontains=query)
        raw_customer_id = query[5:] if query.upper().startswith("CUST-") else query
        if raw_customer_id.isdigit():
            search_filter |= Q(invoice__customer__customer_id=int(raw_customer_id))
        ledgers = ledgers.filter(search_filter)

    summary = ledgers.aggregate(
        total=Coalesce(
            Sum("balance"),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    )
    overdue = ledgers.filter(invoice__due_date__lt=date.today())
    page_obj, querystring = paginate(request, ledgers, per_page=25)
    return render(
        request,
        "receivables.html",
        {
            "receivables": page_obj,
            "page_obj": page_obj,
            "querystring": querystring,
            "q": query,
            "open_total": summary["total"],
            "overdue_total": overdue.aggregate(
                total=Coalesce(
                    Sum("balance"),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                )
            )["total"],
            "overdue_count": overdue.count(),
            "payment_method_choices": CustomerPayment.Method.choices,
        },
    )


@sales_access_required
def sales(request):
    stock = _build_product_stock()
    pending_orders = _pending_orders_queryset()

    if request.method == "POST":
        customer_value = request.POST.get("customer", "").strip()
        phone = request.POST.get("phone", "").strip()
        product_key = request.POST.get("product", "").strip()
        quantity_raw = request.POST.get("quantity", "0").strip()
        price_raw = request.POST.get("price", "0").strip()
        deposit_raw = request.POST.get("deposit", "0").strip()
        payment_method_raw = request.POST.get("payment_method", "CASH").strip().upper()
        sale_type = request.POST.get("sale_type", "instant").strip().lower()
        delivery_date_raw = request.POST.get("delivery_date", "").strip()
        notes = request.POST.get("notes", "").strip()

        errors = []

        customer = _customer_from_sale_value(customer_value)
        if not customer_value:
            errors.append("Customer is required.")
        elif not customer:
            errors.append("Please select a valid active customer.")

        if product_key not in stock:
            errors.append("Please select a valid product.")

        try:
            quantity = Decimal(quantity_raw)
            if quantity <= 0:
                errors.append("Quantity must be greater than zero.")
        except (InvalidOperation, ValueError):
            quantity = Decimal("0")
            errors.append("Please provide a valid quantity.")

        try:
            unit_price = Decimal(price_raw)
            if unit_price < 0:
                errors.append("Price cannot be negative.")
        except (InvalidOperation, ValueError):
            unit_price = Decimal("0")
            errors.append("Please provide a valid price.")

        try:
            deposit = Decimal(deposit_raw or "0")
            if deposit < 0:
                errors.append("Deposit cannot be negative.")
        except (InvalidOperation, ValueError):
            deposit = Decimal("0")
            errors.append("Please provide a valid deposit.")

        payment_method = _payment_method_from_post(payment_method_raw)

        delivery_date = None
        if sale_type == "booking" and delivery_date_raw:
            try:
                delivery_date = date.fromisoformat(delivery_date_raw)
            except ValueError:
                errors.append("Please provide a valid delivery date for bookings.")

        if not errors and quantity > stock[product_key]["available_qty"]:
            errors.append(
                f"Only {stock[product_key]['available_qty']} {stock[product_key]['unit']} of {stock[product_key]['label']} are available."
            )

        if errors:
            for err in errors:
                messages.error(request, err)
        else:
            today = date.today()
            line_total = (quantity * unit_price).quantize(Decimal("0.01"))
            amount_paid = deposit
            product_name = stock[product_key]["label"]
            sale_account = account_by_system_code(SALE_ACCOUNT_SYSTEM_CODES.get(product_key, ""))
            if not sale_account:
                messages.error(request, f"The built-in sales account for {product_name} is missing.")
                return redirect("sales")
            payment_aliases = {
                "CASH": "Cash",
                "MOMO": "Mobile Money",
                "BANK": "Bank",
            }
            payment_label = payment_aliases.get(payment_method, payment_method)
            if amount_paid > 0 and not payment_account_for_method(payment_method):
                messages.error(request, f"The built-in {payment_label} payment account is missing.")
                return redirect("sales")
            if line_total > amount_paid and not ChartOfAccount.objects.filter(account_type__legacy_code="RECEIVABLE", is_active=True).exists():
                messages.error(request, "No receivable account is configured for unpaid or partially paid sales.")
                return redirect("sales")

            wht_amount = (line_total * WHT_RATE).quantize(Decimal("0.01")) if customer and customer.pay_wht else Decimal("0.00")
            customer_amount_due = (line_total - wht_amount).quantize(Decimal("0.01"))
            if amount_paid > customer_amount_due:
                messages.error(
                    request,
                    f"Amount paid cannot exceed {customer_amount_due} because this customer has withholding tax.",
                )
                return redirect("sales")

            credit_error = _credit_validation_error(customer, customer_amount_due - amount_paid)
            if credit_error:
                messages.error(request, credit_error)
                return redirect("sales")

            credit_base_date = delivery_date if sale_type == "booking" and delivery_date else today
            due_date = (
                credit_base_date + timedelta(days=customer.credit_days)
                if customer_amount_due > amount_paid
                else None
            )

            with transaction.atomic():
                if customer:
                    if phone and customer.phone_number != phone:
                        customer.phone_number = phone
                        customer.save(update_fields=["phone_number"])

                balance = (customer_amount_due - amount_paid).quantize(Decimal("0.01"))
                if balance <= 0:
                    invoice_status = SaleInvoice.Status.PAID
                elif sale_type == "booking" and amount_paid == 0:
                    invoice_status = SaleInvoice.Status.DRAFT
                else:
                    invoice_status = SaleInvoice.Status.ISSUED

                invoice = SaleInvoice.objects.create(
                    invoice_no=_next_invoice_no(today),
                    customer=customer,
                    invoice_date=today,
                    due_date=due_date,
                    subtotal=line_total,
                    discount_amount=Decimal("0.00"),
                    total_amount=line_total,
                    wht_amount=wht_amount,
                    payment_method=payment_method,
                    status=invoice_status,
                    delivery_status=(
                        SaleInvoice.DeliveryStatus.DELIVERED
                        if sale_type == "instant"
                        else SaleInvoice.DeliveryStatus.PENDING
                    ),
                    notes=(notes + (f"\nSale type: {sale_type.title()}" if sale_type else "")).strip(),
                    created_by=request.user,
                )

                sale_item = SaleItem.objects.create(
                    invoice=invoice,
                    account=sale_account,
                    product_name=product_name,
                    quantity=quantity,
                    unit=stock[product_key]["unit"],
                    unit_price=unit_price,
                    line_total=line_total,
                )
                
                # Generate accounting code for this sale
                account_name = f"sale_of_{product_name.lower()}"
                
                # Determine prefix based on product type
                if "egg" in product_name.lower() and "layer" not in product_name.lower():
                    prefix = "SE"  # Sales Eggs
                elif "off layer" in product_name.lower() or "off-layer" in product_name.lower() or "offlay" in product_name.lower():
                    prefix = "SO"  # Sales Off-Layer
                else:
                    prefix = "SE"  # Default to Eggs
                
                AccountingCode.create_for(
                    account=sale_account,
                    content_object=sale_item,
                    description=f"{product_name} sale",
                )
                post_sale(sale_item, amount_paid=amount_paid, created_by=request.user)

                receivable = ReceivableLedger.objects.create(
                    invoice=invoice,
                    amount_due=customer_amount_due,
                    amount_paid=amount_paid,
                    balance=balance,
                    last_payment_date=today if amount_paid > 0 else None,
                )
                _add_receivable_entry(
                    receivable=receivable,
                    entry_date=today,
                    entry_type=ReceivableEntry.EntryType.INVOICE,
                    amount=customer_amount_due,
                    balance_after=customer_amount_due,
                    reference=invoice.invoice_no,
                    notes="Invoice issued to customer",
                    created_by=request.user,
                )

                if amount_paid > 0:
                    payment = CustomerPayment.objects.create(
                        invoice=invoice,
                        customer=customer,
                        payment_date=today,
                        method=payment_method,
                        amount=amount_paid,
                        received_by=request.user,
                    )
                    _add_receivable_entry(
                        receivable=receivable,
                        entry_date=today,
                        entry_type=ReceivableEntry.EntryType.PAYMENT,
                        amount=amount_paid,
                        balance_after=balance,
                        reference=f"PAY-{payment.payment_id}",
                        notes="Payment received with sale",
                        created_by=request.user,
                    )

            messages.success(request, f"Sale saved successfully. Invoice {invoice.invoice_no} created.")
            return redirect("sales")

    recent_sales = (
        SaleItem.objects.select_related(
            "invoice",
            "invoice__customer",
            "invoice__created_by",
            "invoice__receivable",
        ).order_by("-item_id")[:10]
    )

    context = {
        "stock_cards": [stock["eggs"], stock["damaged_eggs"], stock["manure"], stock["off_layers"]],
        "recent_sales": recent_sales,
        "pending_orders_preview": pending_orders[:5],
        "customers": Customer.objects.filter(is_active=True).order_by("name"),
        "customer_payment_defaults": {
            str(customer.customer_id): {
                "phone": customer.phone_number,
                "preferred_payment_method": customer.preferred_payment_method,
                "pay_wht": customer.pay_wht,
            }
            for customer in Customer.objects.filter(is_active=True).order_by("name")
        },
        "payment_method_choices": Customer.PaymentMethod.choices,
        **_pending_orders_metrics(pending_orders),
    }
    return render(request, "sales.html", context)


@sales_access_required
def orderview(request):
    pending_orders = _pending_orders_queryset()
    _attach_order_item_displays(pending_orders)
    page_obj, querystring = paginate(request, pending_orders, per_page=15)
    context = {
        "pending_orders": page_obj,
        "page_obj": page_obj,
        "querystring": querystring,
        **_pending_orders_metrics(pending_orders),
    }
    return render(request, "orderview.html", context)


@sales_access_required
def orders(request):
    if request.method == "POST":
        action = request.POST.get("action", "").strip().lower()
        invoice_id = request.POST.get("invoice_id", "").strip()
        if invoice_id:
            invoice = get_object_or_404(
                SaleInvoice.objects.select_related("receivable"),
                pk=invoice_id,
            )

            if action == "record_payment":
                payment_amount_raw = request.POST.get("payment_amount", "0").strip()
                payment_method_raw = request.POST.get("payment_method", "CASH").strip().upper()
                payment_reference = request.POST.get("payment_reference", "").strip()
                payment_notes = request.POST.get("payment_notes", "").strip()

                try:
                    payment_amount = Decimal(payment_amount_raw)
                except (InvalidOperation, ValueError):
                    payment_amount = Decimal("0")

                if payment_amount <= 0:
                    messages.error(request, "Payment amount must be greater than zero.")
                    return redirect("orders")

                if invoice.status == SaleInvoice.Status.CANCELLED or invoice.delivery_status == SaleInvoice.DeliveryStatus.CANCELLED:
                    messages.error(request, f"Order {invoice.invoice_no} is cancelled and cannot receive payments.")
                    return redirect("orders")

                payment_method = {
                    "CASH": CustomerPayment.Method.CASH,
                    "MOBILE MONEY": CustomerPayment.Method.MOMO,
                    "MOMO": CustomerPayment.Method.MOMO,
                    "BANK": CustomerPayment.Method.BANK,
                    "OTHER": CustomerPayment.Method.OTHER,
                }.get(payment_method_raw, CustomerPayment.Method.CASH)

                receivable = getattr(invoice, "receivable", None)
                if not receivable:
                    messages.error(request, "No receivable record found for this order.")
                    return redirect("orders")

                try:
                    with transaction.atomic():
                        _payment, receivable = _record_receivable_payment(
                            invoice=invoice,
                            method=payment_method,
                            amount=payment_amount,
                            reference=payment_reference,
                            notes=payment_notes,
                            received_by=request.user,
                        )
                except ValidationError as exc:
                    messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
                    return redirect("orders")

                if receivable.balance <= 0:
                    messages.success(request, f"Payment received. Order {invoice.invoice_no} is now fully paid.")
                else:
                    messages.success(request, f"Partial payment received for {invoice.invoice_no}.")

            elif action == "deliver":
                if invoice.delivery_status == SaleInvoice.DeliveryStatus.DELIVERED:
                    messages.info(request, f"Order {invoice.invoice_no} is already marked delivered.")
                elif invoice.status == SaleInvoice.Status.CANCELLED or invoice.delivery_status == SaleInvoice.DeliveryStatus.CANCELLED:
                    messages.error(request, f"Order {invoice.invoice_no} is cancelled and cannot be delivered.")
                else:
                    receivable = getattr(invoice, "receivable", None)
                    if receivable and receivable.balance > 0:
                        messages.error(
                            request,
                            f"Order {invoice.invoice_no} is not fully paid. Receive full payment before delivery.",
                        )
                    else:
                        if receivable:
                            _refresh_invoice_financial_status(invoice, receivable)
                        try:
                            with transaction.atomic():
                                post_sale_delivery(invoice, created_by=request.user)
                                invoice.delivery_status = SaleInvoice.DeliveryStatus.DELIVERED
                                invoice.save(update_fields=["delivery_status"])
                        except ValidationError as exc:
                            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
                            return redirect("orders")
                        messages.success(request, f"Order {invoice.invoice_no} marked as delivered.")

            elif action == "cancel":
                cancel_reason = request.POST.get("cancel_reason", "").strip()
                with transaction.atomic():
                    invoice.status = SaleInvoice.Status.CANCELLED
                    invoice.delivery_status = SaleInvoice.DeliveryStatus.CANCELLED
                    if cancel_reason:
                        note_line = f"Cancelled on {date.today().isoformat()}: {cancel_reason}"
                        invoice.notes = (invoice.notes + "\n" + note_line).strip()
                        invoice.save(update_fields=["status", "delivery_status", "notes"])
                    else:
                        invoice.save(update_fields=["status", "delivery_status"])
                messages.success(request, f"Order {invoice.invoice_no} cancelled.")

            else:
                messages.error(request, "Invalid order action.")

        else:
            messages.error(request, "Invalid order action.")
        return redirect("orders")

    pending_orders = _pending_orders_queryset()
    _attach_order_item_displays(pending_orders)
    context = {
        "pending_orders": pending_orders,
        **_pending_orders_metrics(pending_orders),
    }
    return render(request, 'orders.html', context)


@sales_access_required
def record_receivable_payment(request, receivable_id):
    if request.method != "POST":
        return redirect("receivables")

    receivable = get_object_or_404(
        ReceivableLedger.objects.select_related("invoice", "invoice__customer"),
        pk=receivable_id,
        balance__gt=0,
    )
    try:
        amount = Decimal(request.POST.get("payment_amount", "0").strip())
    except (InvalidOperation, ValueError):
        amount = Decimal("0.00")
    method = {
        "CASH": CustomerPayment.Method.CASH,
        "MOBILE MONEY": CustomerPayment.Method.MOMO,
        "MOMO": CustomerPayment.Method.MOMO,
        "BANK": CustomerPayment.Method.BANK,
        "OTHER": CustomerPayment.Method.OTHER,
    }.get(request.POST.get("payment_method", "CASH").strip().upper(), CustomerPayment.Method.CASH)

    try:
        with transaction.atomic():
            _payment, updated_receivable = _record_receivable_payment(
                invoice=receivable.invoice,
                amount=amount,
                method=method,
                reference=request.POST.get("payment_reference", "").strip(),
                notes=request.POST.get("payment_notes", "").strip(),
                received_by=request.user,
            )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
    else:
        state = "fully paid" if updated_receivable.balance <= 0 else "partially paid"
        messages.success(request, f"Payment saved. {updated_receivable.invoice.invoice_no} is now {state}.")
    return redirect("receivables")
