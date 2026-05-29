from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from datetime import date
from decimal import Decimal, InvalidOperation

from finance.models import ExpenseTransaction
from inventory.models import InventoryTransaction
from poultry.models import PoultryHouse
from sales.models import CustomerPayment, ReceivableLedger, SaleInvoice

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
    return render(request, 'reports.html')


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
