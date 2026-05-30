from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from finance.models import ExpenseTransaction
from inventory.models import InventoryTransaction, ReorderRule
from poultry.models import ApprovalStatus, FeedRecord, MortalityRecord, PoultryBatch, PoultryHouse, egg_collection
from sales.models import CustomerPayment, ReceivableLedger, SaleInvoice, SaleItem

from .models import InvestorCapitalTransaction, Role, User, validate_house_assignment

# Create your views here.


def get_post_login_redirect(user):
    if not user.role:
        return "login"

    role_code = (user.role.code or "").upper()
    role_name = (user.role.name or "").strip().lower()

    if role_code == "WORKER":
        return "workersdash"
    if role_code == "SUPERVISOR":
        return "supdash"
    if role_code == "MANAGER":
        return "managerdash"
    if role_code in {"OWNER", "INVESTOR"} or "investor" in role_name:
        return "investor"

    return "login"


def login_view(request):
    """Handle user login."""
    if request.user.is_authenticated:
        redirect_target = get_post_login_redirect(request.user)
        if redirect_target == "login":
            logout(request)
            messages.error(request, "Your account has no assigned role. Please contact administrator.")
            return render(request, "login.html")
        return redirect(redirect_target)
    
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        
        if not username or not password:
            messages.error(request, 'Please provide both username and password.')
            return render(request, 'login.html')
        
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            if user.is_active:
                if user.is_locked:
                    messages.error(request, 'Your account has been locked. Please contact administrator.')
                    return render(request, 'login.html')

                if not user.role:
                    messages.error(request, 'Your account has no assigned role. Please contact administrator.')
                    return render(request, 'login.html')
                
                login(request, user)
                messages.success(request, f'Welcome back, {user.first_name or user.username}!')
                return redirect(get_post_login_redirect(user))
            else:
                messages.error(request, 'Your account has been disabled.')
        else:
            messages.error(request, 'Invalid username or password.')
    
    return render(request, 'login.html')


def signup_view(request):
    """Handle user registration."""
    if request.user.is_authenticated:
        redirect_target = get_post_login_redirect(request.user)
        if redirect_target == "login":
            logout(request)
            messages.error(request, "Your account has no assigned role. Please contact administrator.")
            return redirect("login")
        return redirect(redirect_target)
    
    roles = Role.objects.filter(is_active=True).order_by("name")
    houses = PoultryHouse.objects.filter(is_active=True).order_by("house_code", "name")
    form_data = {}
    selected_house_ids = []
    
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        password = request.POST.get('password', '')
        password_confirm = request.POST.get('password_confirm', '')
        phone_number = request.POST.get('phone_number', '').strip()
        role_id = request.POST.get('role')
        selected_house_ids = request.POST.getlist('houses')

        form_data = {
            "username": username,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "phone_number": phone_number,
            "role": role_id,
        }
        
        # Validation
        errors = []
        role = None
        selected_houses = list(houses.filter(pk__in=selected_house_ids))
        
        if not all([username, email, first_name, password, password_confirm, role_id]):
            errors.append('All fields are required.')
        
        if len(username) < 4:
            errors.append('Username must be at least 4 characters long.')
        
        if User.objects.filter(username=username).exists():
            errors.append('Username already exists.')
        
        if User.objects.filter(email=email).exists():
            errors.append('Email already exists.')
        
        if len(password) < 8:
            errors.append('Password must be at least 8 characters long.')
        
        if password != password_confirm:
            errors.append('Passwords do not match.')
        
        if not any(char.isupper() for char in password):
            errors.append('Password must contain at least one uppercase letter.')
        
        if not any(char.isdigit() for char in password):
            errors.append('Password must contain at least one digit.')

        if role_id:
            role = Role.objects.filter(id=role_id, is_active=True).first()
            if role is None:
                errors.append('Invalid role selected.')

        if len(selected_houses) != len(set(selected_house_ids)):
            errors.append('Please select valid poultry house assignments.')

        if role is not None:
            try:
                validate_house_assignment(role, selected_houses)
            except ValidationError as exc:
                errors.extend(exc.messages)
        
        if errors:
            for error in errors:
                messages.error(request, error)
            return render(
                request,
                'signup.html',
                {
                    'roles': roles,
                    'houses': houses,
                    'form_data': form_data,
                    'selected_house_ids': selected_house_ids,
                },
            )
        
        try:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                phone_number=phone_number,
                role=role
            )
            if selected_houses:
                user.houses.set(selected_houses)
            messages.success(request, 'Account created successfully! Please log in.')
            return redirect('login')
        except Exception as e:
            messages.error(request, f'Error creating account: {str(e)}')
            return render(
                request,
                'signup.html',
                {
                    'roles': roles,
                    'houses': houses,
                    'form_data': form_data,
                    'selected_house_ids': selected_house_ids,
                },
            )
    
    context = {
        'roles': roles,
        'houses': houses,
        'form_data': form_data,
        'selected_house_ids': selected_house_ids,
    }
    return render(request, 'signup.html', context)


def logout_view(request):
    """Handle user logout."""
    logout(request)
    messages.success(request, 'You have been logged out successfully.')
    return redirect('login')


@login_required(login_url='login')
def reports(request):
    today = date.today()

    def parse_date_param(name, fallback):
        raw_value = request.GET.get(name, "").strip()
        if not raw_value:
            return fallback
        try:
            return date.fromisoformat(raw_value)
        except ValueError:
            return fallback

    start_date = parse_date_param("start_date", date(today.year, 1, 1))
    end_date = parse_date_param("end_date", today)
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    selected_group_by = request.GET.get("group_by", "month")
    selected_house = request.GET.get("house", "").strip()
    selected_report = request.GET.get("report_type", "").strip()

    def decimal_total(queryset, field_name):
        return queryset.aggregate(
            total=Coalesce(
                Sum(field_name),
                Value(0),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )["total"]

    def number(value):
        return float(value or 0)

    def grouped_series(queryset, date_field, value_field, group_by, start, end):
        grouped = {}
        current = start
        while current <= end:
            if group_by == "week":
                key_date = current - timedelta(days=current.weekday())
                label = f"Week of {key_date.strftime('%d %b')}"
            elif group_by == "month":
                label = current.strftime("%b %Y")
            else:
                label = current.strftime("%d %b")
            grouped.setdefault(label, Decimal("0"))
            current += timedelta(days=1)

        for row in queryset.values(date_field).annotate(total=Sum(value_field)).order_by(date_field):
            row_date = row[date_field]
            if not row_date:
                continue
            if group_by == "week":
                key_date = row_date - timedelta(days=row_date.weekday())
                label = f"Week of {key_date.strftime('%d %b')}"
            elif group_by == "month":
                label = row_date.strftime("%b %Y")
            else:
                label = row_date.strftime("%d %b")
            grouped[label] = grouped.get(label, Decimal("0")) + (row["total"] or Decimal("0"))
        return {"labels": list(grouped.keys()), "values": [number(value) for value in grouped.values()]}

    sales_qs = SaleInvoice.objects.exclude(status=SaleInvoice.Status.CANCELLED).filter(
        invoice_date__range=(start_date, end_date)
    )
    payments_qs = CustomerPayment.objects.filter(payment_date__range=(start_date, end_date))
    expenses_qs = ExpenseTransaction.objects.exclude(status=ExpenseTransaction.Status.REJECTED).filter(
        expense_date__range=(start_date, end_date)
    )
    eggs_qs = egg_collection.objects.filter(
        collection_date__range=(start_date, end_date),
        status=ApprovalStatus.APPROVED,
    )
    feed_qs = FeedRecord.objects.filter(
        record_date__range=(start_date, end_date),
        status=ApprovalStatus.APPROVED,
    )
    mortality_qs = MortalityRecord.objects.filter(
        record_date__range=(start_date, end_date),
        status=ApprovalStatus.APPROVED,
    )

    if selected_house:
        eggs_qs = eggs_qs.filter(batch__house_id=selected_house)
        feed_qs = feed_qs.filter(batch__house_id=selected_house)
        mortality_qs = mortality_qs.filter(batch__house_id=selected_house)

    total_revenue = decimal_total(sales_qs, "total_amount")
    cash_received = decimal_total(payments_qs, "amount")
    total_expenses = decimal_total(expenses_qs, "total_amount")
    profit = total_revenue - total_expenses
    total_eggs = eggs_qs.aggregate(total=Sum("eggs_collected"))["total"] or 0
    rejected_eggs = eggs_qs.aggregate(total=Sum("eggs_rejected"))["total"] or 0
    feed_used_kg = feed_qs.aggregate(total=Sum("quantity_kg"))["total"] or Decimal("0")
    deaths = mortality_qs.aggregate(total=Sum("number_dead"))["total"] or 0
    outstanding_balances = decimal_total(ReceivableLedger.objects.all(), "balance")
    pending_orders = SaleInvoice.objects.filter(
        delivery_status=SaleInvoice.DeliveryStatus.PENDING
    ).exclude(status=SaleInvoice.Status.CANCELLED).count()

    active_batches = PoultryBatch.objects.filter(status=PoultryBatch.Status.ACTIVE).select_related("house")
    if selected_house:
        active_batches = active_batches.filter(house_id=selected_house)

    current_birds = 0
    for batch in active_batches:
        approved_deaths = batch.mortality_records.filter(
            status=ApprovalStatus.APPROVED
        ).aggregate(total=Sum("number_dead"))["total"] or 0
        birds_sold = SaleItem.objects.filter(batch=batch).filter(
            Q(product_name__icontains="bird") | Q(product_name__icontains="off layer")
        ).aggregate(total=Sum("quantity"))["total"] or 0
        current_birds += max(batch.initial_quantity - approved_deaths - int(birds_sold), 0)

    low_stock_items = 0
    for rule in ReorderRule.objects.filter(alerts_enabled=True).select_related("store", "item"):
        transactions = InventoryTransaction.objects.filter(store=rule.store, item=rule.item)
        stock_in = transactions.filter(tx_type=InventoryTransaction.TxType.IN_).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
        stock_out = transactions.filter(tx_type=InventoryTransaction.TxType.OUT).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
        adjustments = transactions.filter(tx_type=InventoryTransaction.TxType.ADJUST).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
        if stock_in - stock_out + adjustments <= rule.reorder_level:
            low_stock_items += 1

    expense_breakdown = [
        {"label": row["category__name"] or "Uncategorised", "value": number(row["total"])}
        for row in expenses_qs.values("category__name").annotate(total=Sum("total_amount")).order_by("-total")[:8]
    ]
    sales_breakdown = [
        {"label": row["product_name"] or "Sales", "value": number(row["total"])}
        for row in SaleItem.objects.filter(invoice__in=sales_qs)
        .values("product_name")
        .annotate(total=Sum("line_total"))
        .order_by("-total")[:8]
    ]
    mortality_breakdown = [
        {"label": row["cause__name"] or "Unspecified", "value": number(row["total"])}
        for row in mortality_qs.values("cause__name").annotate(total=Sum("number_dead")).order_by("-total")[:8]
    ]
    staff_activity = [
        {"label": row["role__name"] or "No role", "value": row["total"]}
        for row in User.objects.values("role__name").annotate(total=Count("id")).order_by("role__name")
    ]

    inventory_rows = []
    item_ids = InventoryTransaction.objects.values_list("item_id", flat=True).distinct()
    for item_id in item_ids:
        item_transactions = InventoryTransaction.objects.filter(item_id=item_id).select_related("item", "store")
        latest = item_transactions.order_by("-tx_date", "-tx_id").first()
        if not latest:
            continue
        stock_in = item_transactions.filter(tx_type=InventoryTransaction.TxType.IN_).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
        stock_out = item_transactions.filter(tx_type=InventoryTransaction.TxType.OUT).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
        adjustments = item_transactions.filter(tx_type=InventoryTransaction.TxType.ADJUST).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
        inventory_rows.append({
            "item": latest.item.name,
            "unit": latest.item.unit,
            "quantity": stock_in - stock_out + adjustments,
            "last_date": latest.tx_date,
        })
    inventory_rows.sort(key=lambda row: row["item"].lower())

    chart_data = {
        "financial": {
            "labels": grouped_series(sales_qs, "invoice_date", "total_amount", selected_group_by, start_date, end_date)["labels"],
            "datasets": [
                {"label": "Revenue", "values": grouped_series(sales_qs, "invoice_date", "total_amount", selected_group_by, start_date, end_date)["values"]},
                {"label": "Expenses", "values": grouped_series(expenses_qs, "expense_date", "total_amount", selected_group_by, start_date, end_date)["values"]},
                {"label": "Cash received", "values": grouped_series(payments_qs, "payment_date", "amount", selected_group_by, start_date, end_date)["values"]},
            ],
        },
        "production": {
            "labels": grouped_series(eggs_qs, "collection_date", "eggs_collected", selected_group_by, start_date, end_date)["labels"],
            "datasets": [
                {"label": "Eggs collected", "values": grouped_series(eggs_qs, "collection_date", "eggs_collected", selected_group_by, start_date, end_date)["values"]},
                {"label": "Rejected eggs", "values": grouped_series(eggs_qs, "collection_date", "eggs_rejected", selected_group_by, start_date, end_date)["values"]},
                {"label": "Deaths", "values": grouped_series(mortality_qs, "record_date", "number_dead", selected_group_by, start_date, end_date)["values"]},
            ],
        },
        "expenses": expense_breakdown,
        "sales": sales_breakdown,
        "mortality": mortality_breakdown,
        "staff": staff_activity,
    }

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "selected_group_by": selected_group_by,
        "selected_house": selected_house,
        "selected_report": selected_report,
        "houses": PoultryHouse.objects.filter(is_active=True).order_by("house_code", "name"),
        "metrics": {
            "revenue": total_revenue,
            "cash_received": cash_received,
            "expenses": total_expenses,
            "profit": profit,
            "eggs": total_eggs,
            "rejected_eggs": rejected_eggs,
            "feed_used_kg": feed_used_kg,
            "deaths": deaths,
            "current_birds": current_birds,
            "outstanding_balances": outstanding_balances,
            "pending_orders": pending_orders,
            "low_stock_items": low_stock_items,
        },
        "expense_breakdown": expense_breakdown,
        "sales_breakdown": sales_breakdown,
        "mortality_breakdown": mortality_breakdown,
        "inventory_rows": inventory_rows[:20],
        "recent_sales": sales_qs.select_related("customer", "created_by").order_by("-invoice_date", "-created_at")[:8],
        "recent_expenses": expenses_qs.select_related("category", "created_by").order_by("-expense_date", "-created_at")[:8],
        "recent_eggs": eggs_qs.select_related("batch__house", "collected_by").order_by("-collection_date", "-collected_at")[:8],
        "chart_data": chart_data,
    }
    return render(request, 'reports.html', context)


@login_required(login_url='login')
def end_of_day(request):
    # Sample data for demonstration
    eggs = 2000
    sales = 10000000
    expenses = 500000
    deaths = 10
    profit = sales - expenses

    context = {
        'eggs': eggs,
        'sales': sales,
        'expenses': expenses,
        'deaths': deaths,
        'profit': profit,
    }
    return render(request, 'end_of_day.html', context)


@login_required(login_url='login')
def valuation(request):
    if request.method == "POST":
        transaction_type = request.POST.get("transaction_type", "").strip()
        transaction_date_raw = request.POST.get("transaction_date", "").strip()
        amount_raw = request.POST.get("amount", "").strip()
        notes = request.POST.get("notes", "").strip()

        errors = []
        transaction_date = None
        amount = None

        valid_types = dict(InvestorCapitalTransaction.TransactionType.choices)
        if transaction_type not in valid_types:
            errors.append("Please select a valid capital transaction type.")

        if not transaction_date_raw:
            errors.append("Transaction date is required.")
        else:
            try:
                transaction_date = date.fromisoformat(transaction_date_raw)
            except ValueError:
                errors.append("Please enter a valid transaction date.")

        try:
            amount = Decimal(amount_raw)
            if amount <= 0:
                errors.append("Amount must be greater than zero.")
        except (InvalidOperation, ValueError):
            errors.append("Please enter a valid amount.")

        if not errors and transaction_date and amount:
            InvestorCapitalTransaction.objects.create(
                transaction_type=transaction_type,
                transaction_date=transaction_date,
                amount=amount,
                notes=notes,
                recorded_by=request.user,
            )
            messages.success(request, "Investor capital record saved.")
            return redirect("valuation")

        for error in errors:
            messages.error(request, error)

    def decimal_total(queryset, field_name):
        return queryset.aggregate(
            total=Coalesce(
                Sum(field_name),
                Value(0),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )["total"]

    capital_records = InvestorCapitalTransaction.objects.select_related("recorded_by")
    startup_capital = decimal_total(
        capital_records.filter(transaction_type=InvestorCapitalTransaction.TransactionType.STARTUP),
        "amount",
    )
    additional_capital = decimal_total(
        capital_records.filter(transaction_type=InvestorCapitalTransaction.TransactionType.ADDITION),
        "amount",
    )
    withdrawn = decimal_total(
        capital_records.filter(transaction_type=InvestorCapitalTransaction.TransactionType.WITHDRAWAL),
        "amount",
    )
    invested = startup_capital + additional_capital
    net_owner_capital = invested - withdrawn

    sales = decimal_total(
        SaleInvoice.objects.exclude(status=SaleInvoice.Status.CANCELLED),
        "total_amount",
    )
    cash_received = decimal_total(CustomerPayment.objects.all(), "amount")
    expenses = decimal_total(
        ExpenseTransaction.objects.exclude(status=ExpenseTransaction.Status.REJECTED),
        "total_amount",
    )
    profit = sales - expenses
    outstanding_receivables = decimal_total(ReceivableLedger.objects.all(), "balance")

    inventory_rows = {}
    for tx in InventoryTransaction.objects.select_related("item").order_by("item_id", "tx_date", "tx_id"):
        row = inventory_rows.setdefault(
            tx.item_id,
            {"quantity": Decimal("0.000"), "unit_price": Decimal("0.00")},
        )
        if tx.tx_type == InventoryTransaction.TxType.IN_:
            row["quantity"] += tx.quantity
            if tx.unit_price is not None:
                row["unit_price"] = tx.unit_price
        elif tx.tx_type == InventoryTransaction.TxType.OUT:
            row["quantity"] -= tx.quantity
        else:
            row["quantity"] += tx.quantity
    inventory_value = sum(
        max(row["quantity"], Decimal("0.000")) * row["unit_price"]
        for row in inventory_rows.values()
    )

    cash_position = net_owner_capital + cash_received - expenses
    assets = cash_position + outstanding_receivables + inventory_value
    liabilities = Decimal("0.00")
    business_value = assets - liabilities
    owner_equity = business_value
    owner_gain = owner_equity - net_owner_capital
    roi = (owner_gain / invested * 100) if invested > 0 else Decimal("0")

    capital_mix = [
        {"label": "Startup Capital", "value": float(startup_capital)},
        {"label": "Additional Capital", "value": float(additional_capital)},
        {"label": "Withdrawals", "value": float(withdrawn)},
    ]

    asset_mix = [
        {"label": "Cash Position", "value": float(cash_position)},
        {"label": "Receivables", "value": float(outstanding_receivables)},
        {"label": "Inventory Estimate", "value": float(inventory_value)},
    ]

    valuation_notes = []
    if invested <= 0:
        valuation_notes.append("Add startup capital first so ROI and owner equity can be measured properly.")
    if cash_position < 0:
        valuation_notes.append("Cash position is negative. The farm may need cash collection, cost control, or additional capital.")
    if outstanding_receivables > cash_received and cash_received > 0:
        valuation_notes.append("Receivables are higher than collected cash. Follow up customer balances before adding more capital.")
    if profit < 0:
        valuation_notes.append("The farm is carrying a loss. Review expenses and selling prices before expansion.")
    if not valuation_notes:
        valuation_notes.append("Valuation is stable based on the current records. Keep capital additions separated from operating revenue.")

    context = {
        "today": date.today(),
        "startup_capital": startup_capital,
        "additional_capital": additional_capital,
        'invested': invested,
        'withdrawn': withdrawn,
        "net_owner_capital": net_owner_capital,
        "cash_received": cash_received,
        "sales": sales,
        "expenses": expenses,
        'profit': profit,
        'assets': assets,
        'liabilities': liabilities,
        'business_value': business_value,
        "owner_equity": owner_equity,
        "owner_gain": owner_gain,
        'roi': roi,
        "cash_position": cash_position,
        "outstanding_receivables": outstanding_receivables,
        "inventory_value": inventory_value,
        "capital_records": capital_records[:12],
        "capital_mix": capital_mix,
        "asset_mix": asset_mix,
        "valuation_notes": valuation_notes,
        "capital_transaction_types": InvestorCapitalTransaction.TransactionType.choices,
    }
    return render(request, 'valuation.html', context)


@login_required(login_url='login')
def investor_reports(request):
    return render(request, 'investor_reports.html')
