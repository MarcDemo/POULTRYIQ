from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render

from inventory.models import InventoryTransaction
from poultry.models import ApprovalStatus, egg_collection
from .models import Customer, CustomerPayment, ReceivableLedger, SaleInvoice, SaleItem


EGGS_PER_TRAY = Decimal("30")


PRODUCTS = {
    "eggs": {
        "label": "Eggs",
        "unit": "trays",
        "sold_filter": Q(product_name__iexact="Eggs"),
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


@login_required(login_url="login")
def sales(request):
    stock = _build_product_stock()
    pending_orders = _pending_orders_queryset()

    if request.method == "POST":
        customer_name = request.POST.get("customer", "").strip()
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

        if not customer_name:
            errors.append("Customer name is required.")

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

        payment_method = CustomerPayment.Method.CASH
        payment_map = {
            "CASH": CustomerPayment.Method.CASH,
            "MOBILE MONEY": CustomerPayment.Method.MOMO,
            "MOMO": CustomerPayment.Method.MOMO,
            "BANK": CustomerPayment.Method.BANK,
        }
        payment_method = payment_map.get(payment_method_raw, CustomerPayment.Method.CASH)

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
            balance = (line_total - amount_paid).quantize(Decimal("0.01"))
            if balance <= 0:
                invoice_status = SaleInvoice.Status.PAID
            elif sale_type == "booking" and amount_paid == 0:
                invoice_status = SaleInvoice.Status.DRAFT
            else:
                invoice_status = SaleInvoice.Status.ISSUED

            with transaction.atomic():
                customer = Customer.objects.filter(name__iexact=customer_name).first()
                if customer:
                    if phone and customer.phone_number != phone:
                        customer.phone_number = phone
                        customer.save(update_fields=["phone_number"])
                else:
                    customer = Customer.objects.create(name=customer_name, phone_number=phone)

                invoice = SaleInvoice.objects.create(
                    invoice_no=_next_invoice_no(today),
                    customer=customer,
                    invoice_date=today,
                    due_date=delivery_date,
                    subtotal=line_total,
                    discount_amount=Decimal("0.00"),
                    total_amount=line_total,
                    status=invoice_status,
                    delivery_status=(
                        SaleInvoice.DeliveryStatus.DELIVERED
                        if sale_type == "instant"
                        else SaleInvoice.DeliveryStatus.PENDING
                    ),
                    notes=(notes + (f"\nSale type: {sale_type.title()}" if sale_type else "")).strip(),
                    created_by=request.user,
                )

                SaleItem.objects.create(
                    invoice=invoice,
                    product_name=stock[product_key]["label"],
                    quantity=quantity,
                    unit=stock[product_key]["unit"],
                    unit_price=unit_price,
                    line_total=line_total,
                )

                ReceivableLedger.objects.create(
                    invoice=invoice,
                    amount_due=line_total,
                    amount_paid=amount_paid,
                    balance=balance,
                    last_payment_date=today if amount_paid > 0 else None,
                )

                if amount_paid > 0:
                    CustomerPayment.objects.create(
                        invoice=invoice,
                        customer=customer,
                        payment_date=today,
                        method=payment_method,
                        amount=amount_paid,
                        received_by=request.user,
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
        "stock_cards": [stock["eggs"], stock["manure"], stock["off_layers"]],
        "recent_sales": recent_sales,
        "pending_orders_preview": pending_orders[:5],
        **_pending_orders_metrics(pending_orders),
    }
    return render(request, "sales.html", context)


@login_required(login_url="login")
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

                with transaction.atomic():
                    receivable.amount_paid = (receivable.amount_paid + payment_amount).quantize(Decimal("0.01"))
                    receivable.balance = (receivable.amount_due - receivable.amount_paid).quantize(Decimal("0.01"))
                    receivable.last_payment_date = date.today()
                    receivable.save(update_fields=["amount_paid", "balance", "last_payment_date"])

                    CustomerPayment.objects.create(
                        invoice=invoice,
                        customer=invoice.customer,
                        payment_date=date.today(),
                        method=payment_method,
                        amount=payment_amount,
                        reference=payment_reference,
                        notes=payment_notes,
                        received_by=request.user,
                    )

                    _refresh_invoice_financial_status(invoice, receivable)

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
                        invoice.delivery_status = SaleInvoice.DeliveryStatus.DELIVERED
                        invoice.save(update_fields=["delivery_status"])
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
