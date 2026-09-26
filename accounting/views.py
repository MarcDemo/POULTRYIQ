from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.shortcuts import render
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Sum, Q
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from .services import (
    budget_variance_summary,
    change_budget_status,
    create_journal_draft,
    dispose_fixed_asset,
    get_cash_flow_data,
    get_financial_statement_data,
    get_pl_data,
    get_trial_balance_data,
    post_journal_draft,
    post_asset_purchase,
    post_journal_entry,
    revalue_fixed_asset,
    reverse_journal_entry,
    run_depreciation_report,
    ensure_fiscal_periods,
    void_journal_entry,
)
from accounts.models import Role
from .models import (
    BalanceSheetAccount,
    FixedAssetAcquisition,
    AssetConstructionProject,
    AssetConstructionCostLine,
    AssetCategory,
    AccountType,
    Budget,
    BudgetLine,
    ChartOfAccount,
    DepreciationRun,
    FinancialStatement,
    FiscalPeriod,
    FixedAssetDisposal,
    FixedAssetRevaluation,
    JournalEntry,
    PaymentMethod,
)
from inventory.models import Supplier


# Building categories that must be recorded via Asset Construction, not direct acquisition.
CONSTRUCTION_ONLY_CATEGORIES = {
    "POULTRY_HOUSE",
    "BROILER_HOUSE",
    "LAYER_HOUSE",
    "HATCHERY_BUILDING",
    "FEED_STORE",
    "OFFICE_BUILDING",
    "STAFF_QUARTERS",
    "SECURITY_BOOTH",
    "GENERAL_BUILDING",
}

CONSTRUCTION_COST_ITEMS = [
    "Bricks",
    "Sand",
    "Aggregate",
    "Steel/Rods",
    "Timber",
    "Roofing Sheets",
    "Nails and Fasteners",
    "Electrical Wiring",
    "Plumbing Materials",
    "Paint",
    "Flooring",
    "Doors and Windows",
    "Labour - Masonry",
    "Labour - Carpentry",
    "Labour - Electrical",
    "Labour - Plumbing",
    "Labour - General",
    "Architect/Engineer Fees",
    "Permit and Approvals",
    "Site Preparation",
    "Transport and Logistics",
    "Equipment Hire",
    "Fuel",
    "Security",
    "Software Development",
    "Software License",
    "Cloud Services",
    "Hardware Components",
    "Computer Equipment",
    "Vehicle Parts",
]


def _manager_required(request):
    if not hasattr(request.user, 'role') or (request.user.role and request.user.role.code != 'MANAGER'):
        if not request.user.is_superuser:
            messages.error(request, "Access denied: Managers only.")
            return render(request, 'access_denied.html', status=403)
    return None


def _asset_category_choices(include_construction=True):
    categories = AssetCategory.objects.filter(is_active=True)
    if not include_construction:
        categories = categories.filter(is_construction_only=False)
    return [
        {
            "id": category.pk,
            "name": category.name,
            "is_land": category.is_land,
            "is_depreciable": category.is_depreciable,
            "useful_life_years": category.useful_life_years or "",
        }
        for category in categories.order_by("name")
    ]


def _ensure_asset_categories_from_fixed_asset_accounts():
    fixed_asset_accounts = ChartOfAccount.objects.select_related("account_type").filter(
        is_active=True,
        account_type__account_nature=AccountType.AccountNature.ASSET,
        account_type__name__icontains="fixed",
    )
    for account in fixed_asset_accounts.order_by("account_name"):
        is_land = "land" in account.account_name.lower()
        AssetCategory.objects.get_or_create(
            asset_account=account,
            defaults={
                "name": account.account_name,
                "is_land": is_land,
                "is_depreciable": not is_land,
                "useful_life_years": None if is_land else 5,
                "is_active": True,
            },
        )


def _get_asset_category(raw_value):
    if not raw_value:
        return None
    query = Q(legacy_code=raw_value)
    if raw_value.isdigit():
        query |= Q(pk=raw_value)
    return AssetCategory.objects.filter(query, is_active=True).first()


@login_required
def chart_of_accounts(request):
    """
    Display Chart of Accounts (P&L Report) with accounting codes.
    Accessible to managers only.
    """
    denied = _manager_required(request)
    if denied:
        return denied
    
    # Get date range from request
    start_date_str = request.GET.get('start_date', '')
    end_date_str = request.GET.get('end_date', '')
    
    # Parse dates
    today = timezone.now().date()
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            start_date = None
    else:
        start_date = None
    
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            end_date = None
    else:
        end_date = None
    
    # Get P&L data
    pl_data = get_pl_data(start_date, end_date)
    
    context = {
        'pl_data': pl_data,
        'start_date': start_date_str,
        'end_date': end_date_str,
        'start_date_obj': start_date,
        'end_date_obj': end_date,
        'today': today,
    }
    
    return render(request, 'accounting/chart_of_accounts.html', context)



@login_required
def balance_sheet(request):
    denied = _manager_required(request)
    if denied:
        return denied

    # Keep date filtering semantics consistent with P&L.
    start_date_str = request.GET.get('start_date', '').strip()
    end_date_str = request.GET.get('end_date', '').strip()

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            start_date = None
    else:
        start_date = None

    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            end_date = None
    else:
        end_date = None

    # Get BS data using transaction-backed amounts
    from .services import get_bs_data
    bs_data = get_bs_data(start_date, end_date)

    return render(
        request,
        "accounting/balance_sheet.html",
        {
            "grouped_accounts": bs_data['grouped_accounts'],
            "start_date": start_date_str,
            "end_date": end_date_str,
            "start_date_obj": start_date,
            "end_date_obj": end_date,
        },
    )


def _date_range_from_request(request):
    start_date_str = request.GET.get('start_date', '').strip()
    end_date_str = request.GET.get('end_date', '').strip()

    start_date = None
    end_date = None
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            start_date = None
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            end_date = None
    return start_date_str, end_date_str, start_date, end_date


@login_required
def trial_balance(request):
    denied = _manager_required(request)
    if denied:
        return denied

    start_date_str, end_date_str, start_date, end_date = _date_range_from_request(request)
    trial_balance_data = get_trial_balance_data(start_date, end_date)

    return render(
        request,
        "accounting/trial_balance.html",
        {
            "trial_balance_data": trial_balance_data,
            "start_date": start_date_str,
            "end_date": end_date_str,
            "start_date_obj": start_date,
            "end_date_obj": end_date,
        },
    )


@login_required
def cash_flow(request):
    denied = _manager_required(request)
    if denied:
        return denied

    start_date_str, end_date_str, start_date, end_date = _date_range_from_request(request)
    cash_flow_data = get_cash_flow_data(start_date, end_date)

    return render(
        request,
        "accounting/cash_flow.html",
        {
            "cash_flow_data": cash_flow_data,
            "start_date": start_date_str,
            "end_date": end_date_str,
            "start_date_obj": start_date,
            "end_date_obj": end_date,
        },
    )


@login_required
def financial_statement(request, statement_code):
    denied = _manager_required(request)
    if denied:
        return denied

    statement = get_object_or_404(FinancialStatement, code__iexact=statement_code, is_active=True)
    start_date_str, end_date_str, start_date, end_date = _date_range_from_request(request)
    statement_data = get_financial_statement_data(statement, start_date, end_date)

    return render(
        request,
        "accounting/financial_statement.html",
        {
            "statement": statement,
            "statement_data": statement_data,
            "start_date": start_date_str,
            "end_date": end_date_str,
            "start_date_obj": start_date,
            "end_date_obj": end_date,
        },
    )


def _parse_date(raw_value, label):
    try:
        return datetime.strptime((raw_value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError(f"Please provide a valid {label}.")


def _parse_money(raw_value, label, *, allow_zero=True):
    try:
        amount = Decimal((raw_value or "").strip())
    except (InvalidOperation, ValueError):
        raise ValidationError(f"Please enter a valid {label}.")
    if amount < 0 or (not allow_zero and amount <= 0):
        suffix = "greater than zero" if not allow_zero else "zero or greater"
        raise ValidationError(f"{label.capitalize()} must be {suffix}.")
    return amount


def _new_manual_journal_reference(entry_date):
    """Reference is readable; the FDN remains the authoritative unique key."""
    return f"JRN-{entry_date:%Y%m%d}-{uuid4().hex[:8].upper()}"


def _posted_journal_lines_from_request(request):
    account_ids = request.POST.getlist("account")
    debits = request.POST.getlist("debit")
    credits = request.POST.getlist("credit")
    memos = request.POST.getlist("memo")
    lines = []
    for index, account_id in enumerate(account_ids):
        account_id = (account_id or "").strip()
        debit_raw = debits[index] if index < len(debits) else ""
        credit_raw = credits[index] if index < len(credits) else ""
        memo = memos[index] if index < len(memos) else ""
        # A blank spare UI row is harmless; a partially completed row is not.
        if not account_id and not (debit_raw or "").strip() and not (credit_raw or "").strip() and not (memo or "").strip():
            continue
        if not account_id:
            raise ValidationError(f"Line {index + 1}: select a ledger account.")
        account = ChartOfAccount.objects.filter(pk=account_id, is_active=True).first()
        if not account:
            raise ValidationError(f"Line {index + 1}: select an active ledger account.")
        try:
            debit = Decimal((debit_raw or "0").strip() or "0")
            credit = Decimal((credit_raw or "0").strip() or "0")
        except (InvalidOperation, ValueError):
            raise ValidationError(f"Line {index + 1}: debit and credit must be valid amounts.")
        lines.append({"account": account, "debit": debit, "credit": credit, "memo": (memo or "").strip()})
    return lines


@login_required
def journal_entries(request):
    """Manager-only controlled journal entry screen and audit register."""
    denied = _manager_required(request)
    if denied:
        return denied

    if request.method == "POST":
        action = request.POST.get("action", "post").strip()
        try:
            if action in {"post", "draft"}:
                entry_date = _parse_date(request.POST.get("entry_date"), "journal date")
                description = request.POST.get("description", "").strip()
                if not description:
                    raise ValidationError("A journal description is required.")
                reference = request.POST.get("reference", "").strip() or _new_manual_journal_reference(entry_date)
                lines = _posted_journal_lines_from_request(request)
                if action == "draft":
                    entry = create_journal_draft(
                        entry_date=entry_date,
                        reference=reference,
                        description=description,
                        lines=lines,
                        created_by=request.user,
                    )
                    messages.success(request, f"Draft {entry.fdn} saved. It has no accounting effect until posted.")
                else:
                    entry = post_journal_entry(
                        entry_date=entry_date,
                        reference=reference,
                        description=description,
                        lines=lines,
                        created_by=request.user,
                        dedupe_source=False,
                    )
                    messages.success(request, f"Balanced journal {entry.fdn} posted.")
            elif action == "post_draft":
                entry = get_object_or_404(JournalEntry, pk=request.POST.get("entry_id"))
                entry = post_journal_draft(entry, posted_by=request.user)
                messages.success(request, f"Draft {entry.fdn} posted.")
            elif action == "void":
                entry = get_object_or_404(JournalEntry, pk=request.POST.get("entry_id"))
                entry = void_journal_entry(
                    entry,
                    voided_by=request.user,
                    reason=request.POST.get("reason", ""),
                )
                messages.success(request, f"Draft {entry.fdn} was voided and retained in the audit trail.")
            elif action == "reverse":
                entry = get_object_or_404(JournalEntry, pk=request.POST.get("entry_id"))
                reversal_date = _parse_date(request.POST.get("reversal_date"), "reversal date")
                reversal = reverse_journal_entry(
                    entry,
                    reversal_date=reversal_date,
                    created_by=request.user,
                    description=request.POST.get("reversal_description", ""),
                )
                messages.success(request, f"Reversal {reversal.fdn} was posted; the original remains unchanged.")
            else:
                raise ValidationError("Unknown journal action.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
        return redirect("journal_entries")

    entries = JournalEntry.objects.select_related("created_by", "reversal_of").prefetch_related(
        "lines__account"
    ).order_by("-entry_date", "-id")[:100]
    return render(
        request,
        "accounting/journal_entries.html",
        {
            "entries": entries,
            "accounts": ChartOfAccount.objects.filter(is_active=True).select_related("account_type").order_by("code"),
            "today": timezone.localdate(),
        },
    )


def _next_budget_version(fiscal_year):
    current = Budget.objects.filter(fiscal_year=fiscal_year).aggregate(maximum=Max("version"))["maximum"]
    return (current or 0) + 1


@login_required
def budgets(request):
    """Budget setup and a live actual-vs-budget view from posted ledgers."""
    denied = _manager_required(request)
    if denied:
        return denied

    selected_budget = None
    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        try:
            if action == "create_budget":
                fiscal_year_raw = request.POST.get("fiscal_year", "").strip()
                try:
                    fiscal_year = int(fiscal_year_raw)
                except ValueError:
                    raise ValidationError("Please provide a valid fiscal year.")
                if fiscal_year < 2000 or fiscal_year > 9999:
                    raise ValidationError("Please provide a valid fiscal year.")
                version_raw = request.POST.get("version", "").strip()
                try:
                    version = int(version_raw) if version_raw else _next_budget_version(fiscal_year)
                except ValueError:
                    raise ValidationError("Budget version must be a whole number.")
                name = request.POST.get("name", "").strip() or f"FY {fiscal_year} Budget v{version}"
                selected_budget = Budget.objects.create(
                    name=name,
                    fiscal_year=fiscal_year,
                    version=version,
                    notes=request.POST.get("notes", "").strip(),
                    created_by=request.user,
                )
                ensure_fiscal_periods(fiscal_year)
                messages.success(request, f"Budget {selected_budget} created.")
            elif action == "save_line":
                selected_budget = get_object_or_404(Budget, pk=request.POST.get("budget_id"))
                if not selected_budget.is_editable:
                    raise ValidationError("This budget is approved or locked. Create a new version to change it.")
                account = get_object_or_404(ChartOfAccount, pk=request.POST.get("account_id"), is_active=True)
                period = get_object_or_404(
                    FiscalPeriod,
                    pk=request.POST.get("fiscal_period_id"),
                    fiscal_year=selected_budget.fiscal_year,
                )
                amount = _parse_money(request.POST.get("amount"), "budget amount")
                line, created = BudgetLine.objects.get_or_create(
                    budget=selected_budget,
                    account=account,
                    fiscal_period=period,
                    defaults={"amount": amount, "notes": request.POST.get("notes", "").strip()},
                )
                if not created:
                    line.amount = amount
                    line.notes = request.POST.get("notes", "").strip()
                    line.save(update_fields=["amount", "notes", "updated_at"])
                messages.success(request, "Budget line saved.")
            elif action in {"approve", "lock"}:
                selected_budget = get_object_or_404(Budget, pk=request.POST.get("budget_id"))
                selected_budget = change_budget_status(
                    selected_budget,
                    status=Budget.Status.APPROVED if action == "approve" else Budget.Status.LOCKED,
                    changed_by=request.user,
                )
                messages.success(request, f"Budget is now {selected_budget.get_status_display().lower()}.")
            else:
                raise ValidationError("Unknown budget action.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
        except Exception as exc:
            # Integrity errors (for example a duplicate version) should be actionable,
            # not a raw database page for managers.
            messages.error(request, str(exc))
        if selected_budget:
            return redirect(f"{request.path}?budget={selected_budget.pk}")
        return redirect("budgets")

    budgets_qs = Budget.objects.all().order_by("-fiscal_year", "-version", "name")
    selected_id = request.GET.get("budget", "").strip()
    if selected_id:
        selected_budget = budgets_qs.filter(pk=selected_id).first()
    if not selected_budget:
        selected_budget = budgets_qs.first()
    periods = []
    variance = None
    if selected_budget:
        periods = ensure_fiscal_periods(selected_budget.fiscal_year)
        variance = budget_variance_summary(selected_budget)

    return render(
        request,
        "accounting/budgets.html",
        {
            "budgets": budgets_qs,
            "selected_budget": selected_budget,
            "periods": periods,
            "accounts": ChartOfAccount.objects.filter(is_active=True).select_related("account_type").order_by("code"),
            "variance": variance,
            "current_year": timezone.localdate().year,
        },
    )


def _asset_payment_selection(raw_value):
    """Resolve the controlled payment dropdown while accepting legacy labels."""
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return None, ""
    if raw_value.upper() == "CREDIT":
        return None, "CREDIT"
    option = None
    if raw_value.startswith("payment-"):
        option = PaymentMethod.objects.filter(
            pk=raw_value.removeprefix("payment-"),
            is_active=True,
        ).first()
    elif raw_value.isdigit():
        option = PaymentMethod.objects.filter(pk=raw_value, is_active=True).first()
    else:
        option = PaymentMethod.objects.filter(name__iexact=raw_value, is_active=True).first()
    if not option:
        raise ValidationError("Please select a valid payment method.")
    return option, option.name


@login_required
def fixed_assets(request):
    denied = _manager_required(request)
    if denied:
        return denied

    _ensure_asset_categories_from_fixed_asset_accounts()

    if request.method == "POST":
        asset_name = request.POST.get("asset_name", "").strip()
        asset_category_raw = request.POST.get("asset_category", "").strip()
        acquisition_date_raw = request.POST.get("acquisition_date", "").strip()
        in_service_date_raw = request.POST.get("in_service_date", "").strip()
        amount_raw = request.POST.get("amount", "").strip()
        residual_value_raw = request.POST.get("residual_value", "0").strip()
        useful_life_years_raw = request.POST.get("useful_life_years", "").strip()
        is_depreciable = request.POST.get("is_depreciable") == "on"
        payment_method_option = None
        payment_method = ""
        supplier = None
        notes = request.POST.get("notes", "").strip()

        errors = []
        if not asset_name:
            errors.append("Asset name is required.")

        asset_category = _get_asset_category(asset_category_raw)
        if not asset_category:
            errors.append("Please select a valid asset category.")

        try:
            acquisition_date = datetime.strptime(acquisition_date_raw, "%Y-%m-%d").date()
        except ValueError:
            acquisition_date = None
            errors.append("Please provide a valid acquisition date.")

        try:
            amount = Decimal(amount_raw)
            if amount <= 0:
                errors.append("Amount must be greater than zero.")
        except (InvalidOperation, ValueError):
            amount = None
            errors.append("Please enter a valid amount.")

        try:
            residual_value = Decimal(residual_value_raw or "0")
            if residual_value < 0:
                errors.append("Residual value cannot be negative.")
        except (InvalidOperation, ValueError):
            residual_value = Decimal("0.00")
            errors.append("Please enter a valid residual value.")

        if in_service_date_raw:
            try:
                in_service_date = datetime.strptime(in_service_date_raw, "%Y-%m-%d").date()
            except ValueError:
                in_service_date = None
                errors.append("Please provide a valid in-service date.")
        else:
            in_service_date = None

        useful_life_years = None
        if is_depreciable:
            try:
                useful_life_years = int(useful_life_years_raw)
                if useful_life_years <= 0:
                    errors.append("Useful life must be greater than zero.")
            except (TypeError, ValueError):
                errors.append("Please enter a valid useful life in years.")

        try:
            payment_method_option, payment_method = _asset_payment_selection(request.POST.get("payment_method"))
            if not payment_method:
                errors.append("Please select a payment method.")
        except ValidationError as exc:
            errors.extend(exc.messages)

        supplier_id = request.POST.get("supplier_id", "").strip()
        if supplier_id:
            supplier = Supplier.objects.filter(pk=supplier_id, is_active=True).first()
            if not supplier:
                errors.append("Please select a valid active supplier.")

        if errors:
            for err in errors:
                messages.error(request, err)
        else:
            try:
                with transaction.atomic():
                    asset = FixedAssetAcquisition.objects.create(
                        asset_name=asset_name,
                        asset_category=asset_category,
                        acquisition_date=acquisition_date,
                        amount=amount,
                        is_depreciable=is_depreciable,
                        useful_life_years=useful_life_years,
                        residual_value=residual_value,
                        in_service_date=in_service_date or acquisition_date,
                        payment_method=payment_method,
                        payment_method_option=payment_method_option,
                        supplier=supplier,
                        notes=notes,
                        created_by=request.user,
                    )
                    post_asset_purchase(asset, created_by=request.user)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
                return redirect("fixed_assets")
            messages.success(request, "Fixed asset acquisition recorded.")
            return redirect("fixed_assets")

    acquisitions = FixedAssetAcquisition.objects.filter(is_active=True).select_related(
        "asset_category",
        "payment_method_option",
        "supplier",
    ).order_by("-acquisition_date", "-id")
    return render(
        request,
        "accounting/fixed_assets.html",
        {
            "asset_categories": _asset_category_choices(),
            "acquisitions": acquisitions,
            "payment_methods": PaymentMethod.objects.filter(is_active=True).select_related("account").order_by("name"),
            "suppliers": Supplier.objects.filter(is_active=True).order_by("name"),
            "today": datetime.now().date(),
        },
    )


@login_required
def depreciation_run(request):
    """Run a selected monthly batch and display its persisted report."""
    denied = _manager_required(request)
    if denied:
        return denied

    today = timezone.localdate()
    selected_year = today.year
    selected_month = today.month
    raw_period = request.GET.get("period", "").strip()
    if raw_period:
        try:
            selected_year, selected_month = (int(value) for value in raw_period.split("-", 1))
            if not 1 <= selected_month <= 12:
                raise ValueError
        except ValueError:
            messages.error(request, "Please choose a valid depreciation month.")
            selected_year, selected_month = today.year, today.month

    if request.method == "POST":
        try:
            period = request.POST.get("period", "").strip()
            selected_year, selected_month = (int(value) for value in period.split("-", 1))
            if not 1 <= selected_month <= 12:
                raise ValueError
            summary = run_depreciation_report(
                year=selected_year,
                month=selected_month,
                created_by=request.user,
            )
            messages.success(
                request,
                f"Depreciation run completed: {summary['posted']} entry/entries posted, "
                f"UGX {summary['total_amount']:,.2f}.",
            )
        except (ValidationError, ValueError) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else "Please choose a valid depreciation month.")
        return redirect(f"{request.path}?period={selected_year:04d}-{selected_month:02d}")

    run = DepreciationRun.objects.filter(period_year=selected_year, period_month=selected_month).first()
    start, end = _month_bounds_for_view(selected_year, selected_month)
    entries = JournalEntry.objects.filter(
        status=JournalEntry.Status.POSTED,
        entry_date__gte=start,
        entry_date__lte=end,
        reference__startswith="DEP-FA-",
    ).prefetch_related("lines__account").order_by("reference")
    return render(
        request,
        "accounting/depreciation_run.html",
        {
            "period": f"{selected_year:04d}-{selected_month:02d}",
            "run": run,
            "entries": entries,
            "runs": DepreciationRun.objects.all()[:24],
            "today": today,
        },
    )


def _month_bounds_for_view(year, month):
    """Use only stdlib date parsing in the view layer."""
    import calendar
    from datetime import date

    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


@login_required
def asset_disposal(request, asset_id):
    denied = _manager_required(request)
    if denied:
        return denied

    asset = get_object_or_404(
        FixedAssetAcquisition.objects.select_related("asset_category", "supplier"),
        pk=asset_id,
        is_active=True,
    )
    if request.method == "POST":
        try:
            disposal_date = _parse_date(request.POST.get("disposal_date"), "disposal date")
            proceeds = _parse_money(request.POST.get("proceeds", "0"), "disposal proceeds")
            payment_option, payment_method = _asset_payment_selection(request.POST.get("payment_method"))
            if proceeds > 0 and not payment_method:
                raise ValidationError("Select how the disposal proceeds were received.")
            disposal = dispose_fixed_asset(
                asset=asset,
                disposal_date=disposal_date,
                proceeds=proceeds,
                payment_method_option=payment_option,
                payment_method=payment_method,
                reason=request.POST.get("reason", "").strip(),
                created_by=request.user,
            )
            messages.success(
                request,
                f"Asset disposed. Journal {disposal.journal_entry.fdn} records a "
                f"{'gain' if disposal.gain_loss >= 0 else 'loss'} of UGX {abs(disposal.gain_loss):,.2f}.",
            )
            return redirect("fixed_assets")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))

    return render(
        request,
        "accounting/asset_disposal.html",
        {
            "asset": asset,
            "payment_methods": PaymentMethod.objects.filter(is_active=True).order_by("name"),
            "today": timezone.localdate(),
        },
    )


@login_required
def asset_revaluation(request, asset_id):
    denied = _manager_required(request)
    if denied:
        return denied

    asset = get_object_or_404(
        FixedAssetAcquisition.objects.select_related("asset_category"),
        pk=asset_id,
        is_active=True,
    )
    if request.method == "POST":
        try:
            revaluation = revalue_fixed_asset(
                asset=asset,
                revaluation_date=_parse_date(request.POST.get("revaluation_date"), "revaluation date"),
                new_value=_parse_money(request.POST.get("new_value"), "new carrying value"),
                reason=request.POST.get("reason", "").strip(),
                created_by=request.user,
            )
            messages.success(
                request,
                f"Revaluation posted as {revaluation.journal_entry.fdn}. "
                f"Adjustment: UGX {revaluation.adjustment:,.2f}.",
            )
            return redirect("fixed_assets")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))

    return render(
        request,
        "accounting/asset_revaluation.html",
        {
            "asset": asset,
            "today": timezone.localdate(),
        },
    )


@login_required
def asset_construction_projects(request):
    denied = _manager_required(request)
    if denied:
        return denied

    _ensure_asset_categories_from_fixed_asset_accounts()

    if request.method == "POST":
        project_name = request.POST.get("project_name", "").strip()
        asset_category_raw = request.POST.get("asset_category", "").strip()
        start_date_raw = request.POST.get("start_date", "").strip()
        in_service_date_raw = request.POST.get("in_service_date", "").strip()
        residual_value_raw = request.POST.get("residual_value", "0").strip()
        useful_life_years_raw = request.POST.get("useful_life_years", "").strip()
        is_depreciable = request.POST.get("is_depreciable") == "on"
        notes = request.POST.get("notes", "").strip()

        errors = []
        if not project_name:
            errors.append("Project name is required.")
        asset_category = _get_asset_category(asset_category_raw)
        if not asset_category:
            errors.append("Please select a valid destination category.")
        try:
            start_date = datetime.strptime(start_date_raw, "%Y-%m-%d").date()
        except ValueError:
            start_date = None
            errors.append("Please provide a valid project start date.")

        if in_service_date_raw:
            try:
                in_service_date = datetime.strptime(in_service_date_raw, "%Y-%m-%d").date()
            except ValueError:
                in_service_date = None
                errors.append("Please provide a valid in-service date.")
        else:
            in_service_date = None

        try:
            residual_value = Decimal(residual_value_raw or "0")
            if residual_value < 0:
                errors.append("Residual value cannot be negative.")
        except (InvalidOperation, ValueError):
            residual_value = Decimal("0.00")
            errors.append("Please provide a valid residual value.")

        useful_life_years = None
        if is_depreciable:
            try:
                useful_life_years = int(useful_life_years_raw)
                if useful_life_years <= 0:
                    errors.append("Useful life must be greater than zero.")
            except (TypeError, ValueError):
                errors.append("Please provide useful life in years.")

        if errors:
            for err in errors:
                messages.error(request, err)
        else:
            try:
                AssetConstructionProject.objects.create(
                    project_name=project_name,
                    asset_category=asset_category,
                    start_date=start_date,
                    in_service_date=in_service_date,
                    is_depreciable=is_depreciable,
                    useful_life_years=useful_life_years,
                    residual_value=residual_value,
                    notes=notes,
                    created_by=request.user,
                )
                messages.success(request, "Construction project account created.")
                return redirect("asset_construction_projects")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))

    projects = AssetConstructionProject.objects.all().order_by("-created_at")
    return render(
        request,
        "accounting/asset_construction_projects.html",
        {
            "asset_categories": _asset_category_choices(),
            "projects": projects,
        },
    )


@login_required
def asset_construction_project_detail(request, project_id):
    denied = _manager_required(request)
    if denied:
        return denied

    project = get_object_or_404(AssetConstructionProject, pk=project_id)

    if request.method == "POST":
        action = request.POST.get("action", "add_cost").strip()

        if action == "complete":
            if project.status != AssetConstructionProject.Status.IN_PROGRESS:
                messages.error(request, "Only in-progress projects can be completed.")
                return redirect("asset_construction_project_detail", project_id=project.pk)

            if project.total_cost <= 0:
                messages.error(request, "Record at least one construction cost before completing the project.")
                return redirect("asset_construction_project_detail", project_id=project.pk)

            complete_date_raw = request.POST.get("completed_date", "").strip()
            try:
                completed_date = datetime.strptime(complete_date_raw, "%Y-%m-%d").date()
            except ValueError:
                completed_date = None
                messages.error(request, "Please provide a valid completion date.")
                return redirect("asset_construction_project_detail", project_id=project.pk)

            try:
                with transaction.atomic():
                    project.status = AssetConstructionProject.Status.COMPLETED
                    project.completed_date = completed_date
                    project.capitalized_on = completed_date
                    project.capitalized_amount = project.total_cost
                    project.save(update_fields=["status", "completed_date", "capitalized_on", "capitalized_amount", "updated_at"])

                    if not hasattr(project, "capitalized_asset"):
                        FixedAssetAcquisition.objects.create(
                            asset_name=project.project_name,
                            asset_category=project.asset_category,
                            source_project=project,
                            acquisition_date=completed_date,
                            amount=project.total_cost,
                            is_depreciable=project.is_depreciable,
                            useful_life_years=project.useful_life_years,
                            residual_value=project.residual_value,
                            in_service_date=project.in_service_date or completed_date,
                            payment_method="Capitalized Construction",
                            notes=f"Capitalized from construction project {project.project_name}",
                            created_by=request.user,
                        )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
                return redirect("asset_construction_project_detail", project_id=project.pk)
            messages.success(request, "Project marked as completed. Cost has been capitalized into fixed assets reporting.")
            return redirect("asset_construction_project_detail", project_id=project.pk)

        if project.status != AssetConstructionProject.Status.IN_PROGRESS:
            messages.error(request, "Cannot add costs to a completed project.")
            return redirect("asset_construction_project_detail", project_id=project.pk)

        cost_date_raw = request.POST.get("cost_date", "").strip()
        selected_cost_item = request.POST.get("cost_item_name", "").strip()
        custom_cost_item = request.POST.get("custom_cost_item_name", "").strip()
        amount_raw = request.POST.get("amount", "").strip()
        notes = request.POST.get("notes", "").strip()

        if selected_cost_item == "OTHER":
            cost_item_name = custom_cost_item
        else:
            cost_item_name = selected_cost_item

        errors = []
        try:
            cost_date = datetime.strptime(cost_date_raw, "%Y-%m-%d").date()
        except ValueError:
            cost_date = None
            errors.append("Please provide a valid cost date.")

        if not cost_item_name:
            errors.append("Cost item name is required.")

        try:
            amount = Decimal(amount_raw)
            if amount <= 0:
                errors.append("Amount must be greater than zero.")
        except (InvalidOperation, ValueError):
            amount = None
            errors.append("Please enter a valid amount.")

        if errors:
            for err in errors:
                messages.error(request, err)
        else:
            try:
                AssetConstructionCostLine.objects.create(
                    project=project,
                    cost_date=cost_date,
                    cost_item_name=cost_item_name,
                    amount=amount,
                    notes=notes,
                    created_by=request.user,
                )
                messages.success(request, "Construction cost recorded.")
                return redirect("asset_construction_project_detail", project_id=project.pk)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))

    cost_lines = project.cost_lines.all().order_by("-cost_date", "-id")
    return render(
        request,
        "accounting/asset_construction_project_detail.html",
        {
            "project": project,
            "cost_lines": cost_lines,
            "construction_cost_items": CONSTRUCTION_COST_ITEMS,
            "today": datetime.now().date(),
        },
    )
