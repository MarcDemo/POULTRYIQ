from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.shortcuts import render
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum, Q
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from .services import (
    get_cash_flow_data,
    get_financial_statement_data,
    get_pl_data,
    get_trial_balance_data,
    post_asset_purchase,
)
from accounts.models import Role
from .models import (
    BalanceSheetAccount,
    FixedAssetAcquisition,
    AssetConstructionProject,
    AssetConstructionCostLine,
    AssetCategory,
    AccountType,
    ChartOfAccount,
    FinancialStatement,
)


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
        payment_method = request.POST.get("payment_method", "").strip()
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
                        notes=notes,
                        created_by=request.user,
                    )
                    post_asset_purchase(asset, created_by=request.user)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
                return redirect("fixed_assets")
            messages.success(request, "Fixed asset acquisition recorded.")
            return redirect("fixed_assets")

    acquisitions = FixedAssetAcquisition.objects.filter(is_active=True).order_by("-acquisition_date", "-id")
    return render(
        request,
        "accounting/fixed_assets.html",
        {
            "asset_categories": _asset_category_choices(),
            "acquisitions": acquisitions,
            "today": datetime.now().date(),
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
