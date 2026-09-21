import json

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Avg, Count, DecimalField, Max, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.utils.timezone import is_naive, localdate, localtime, make_aware, now
from django.views.decorators.http import require_http_methods, require_POST
from datetime import date, timedelta
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from accounts.decorators import worker_required, supervisor_required
from accounts.models import InvestorCapitalTransaction, User
from accounting.models import AccountingCode
from accounting.services import post_expense
from alerts.models import Alert
from expenses.models import ExpenseAllocation, ExpenseCategory, ExpenseTransaction
from payroll.models import SalaryPayment
from health.models import HealthEvent, SickbayCleaningRecord, SicknessReport, TreatmentPlanItem, VaccinationSchedule
from hr.models import Attendance, WagePayment, WelfareRequest, Worker
from sales.views import _build_product_stock
from inventory.models import InventoryTransaction, Item, ReorderRule, Store
from sales.models import CustomerPayment, ReceivableLedger, SaleInvoice, SaleItem
from poultryiq.pagination import paginate
from .models import (
    PoultryBatch,
    DailyProduction,
    PoultryHouse,
    egg_collection,
    FeedRecord,
    FeedFormulaTemplate,
    FeedFormulaIngredient,
    FeedMixture,
    FeedMixtureAllocation,
    FeedMixtureIngredient,
    FlockStage,
    InvestorKpiTarget,
    flock_stage_for_age_days,
    CleaningRecord,
    CleaningPhoto,
    MortalityRecord,
    ApprovalStatus,
)
from .forms import PoultryBatchForm
from .services.investor_analysis import (
    DIMENSIONS as INVESTOR_DIMENSIONS,
    GUIDED_QUESTIONS,
    METRIC_CATALOG,
    build_report,
    catalogue_payload,
    create_target_version,
    target_payload,
)


def _batch_cycle_stage(batch):
    age_days = batch.current_age_days
    lifecycle_days = 560
    stages = [
        {"key": "first_lay", "label": "First lay", "color": "cycle-blue"},
        {"key": "peak_laying", "label": "Peak laying", "color": "cycle-green"},
        {"key": "reduced_laying", "label": "Reduced laying", "color": "cycle-orange"},
        {"key": "off_laying", "label": "Off laying", "color": "cycle-red"},
    ]

    if batch.status == PoultryBatch.Status.CLOSED or age_days > 560:
        active_index = 3
    elif age_days > 350:
        active_index = 2
    elif age_days > 154:
        active_index = 1
    else:
        active_index = 0

    if batch.status == PoultryBatch.Status.CLOSED:
        progress_percent = 100
    else:
        bounded_age = max(0, min(age_days, lifecycle_days))
        progress_percent = int(round((bounded_age / lifecycle_days) * 100))

    return {
        "label": stages[active_index]["label"],
        "color": stages[active_index]["color"],
        "progress_percent": progress_percent,
        "segments": [
            {
                **stage,
                "active": index == active_index,
                "complete": index < active_index,
            }
            for index, stage in enumerate(stages)
        ],
    }


# Create your views here.
@login_required(login_url="login")
def dashboard(request):
    role_code = _role_code(request.user)
    if role_code != "MANAGER":
        messages.error(request, "Access denied: Managers only.")
        from accounts.views import get_post_login_redirect

        return redirect(get_post_login_redirect(request.user))

    today = date.today()
    current_time = now()

    active_batches = PoultryBatch.objects.filter(status=PoultryBatch.Status.ACTIVE)
    total_birds = 0
    for batch in active_batches:
        approved_deaths = batch.mortality_records.filter(
            status=ApprovalStatus.APPROVED
        ).aggregate(total=Sum("number_dead"))["total"] or 0
        total_birds += max(batch.initial_quantity - approved_deaths, 0)

    eggs_today = egg_collection.objects.filter(
        collection_date=today,
        status=ApprovalStatus.APPROVED,
    ).aggregate(total=Sum("eggs_collected"))["total"] or 0

    eggs_stock = _build_product_stock()["eggs"]

    feed_used_today = FeedRecord.objects.filter(
        record_date=today,
        status=ApprovalStatus.APPROVED,
    ).aggregate(total=Sum("quantity_kg"))["total"] or 0

    feed_inventory = InventoryTransaction.objects.filter(
        item__category__code__iexact="FEED"
    )
    feed_stock_in = feed_inventory.filter(tx_type=InventoryTransaction.TxType.IN_).aggregate(
        total=Coalesce(
            Sum("quantity"),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=3),
        )
    )["total"]
    feed_stock_out = feed_inventory.filter(tx_type=InventoryTransaction.TxType.OUT).aggregate(
        total=Coalesce(
            Sum("quantity"),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=3),
        )
    )["total"]
    feed_adjustments = feed_inventory.filter(tx_type=InventoryTransaction.TxType.ADJUST).aggregate(
        total=Coalesce(
            Sum("quantity"),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=3),
        )
    )["total"]
    feed_stock = feed_stock_in - feed_stock_out + feed_adjustments

    pending_eggs = egg_collection.objects.filter(status=ApprovalStatus.PENDING).count()
    pending_feed = FeedRecord.objects.filter(status=ApprovalStatus.PENDING).count()
    pending_cleaning = CleaningRecord.objects.filter(status=ApprovalStatus.PENDING).count()
    pending_mortality = MortalityRecord.objects.filter(status=ApprovalStatus.PENDING).count()

    active_alerts = (
        Alert.objects.filter(
            Q(status=Alert.Status.UNREAD)
            | Q(
                persist_until_resolved=True,
                status__in=[Alert.Status.READ, Alert.Status.ACKNOWLEDGED],
            )
        )
        .select_related("receiver", "sender", "alert_type", "related_house", "related_batch")
        .order_by("-created_at")
    )

    supervisor_alerts = active_alerts.filter(
        receiver__role__code__in=["SUPERVISOR", "MANAGER", "OWNER"]
    )
    upcoming_treatment_items = (
        TreatmentPlanItem.objects.filter(is_given=False)
        .select_related("sickness_report__house_ref", "sickness_report__batch", "alert")
        .order_by("scheduled_for", "-created_at")[:5]
    )
    upcoming_vaccinations = (
        VaccinationSchedule.objects.filter(status=VaccinationSchedule.Status.SCHEDULED)
        .select_related("house_ref", "batch", "alert")
        .order_by("scheduled_for", "-created_at")[:5]
    )

    low_stock_items = 0
    for rule in ReorderRule.objects.filter(alerts_enabled=True).select_related("store", "item"):
        item_transactions = InventoryTransaction.objects.filter(
            store=rule.store,
            item=rule.item,
        )
        item_stock_in = item_transactions.filter(tx_type=InventoryTransaction.TxType.IN_).aggregate(
            total=Coalesce(
                Sum("quantity"),
                Value(0),
                output_field=DecimalField(max_digits=14, decimal_places=3),
            )
        )["total"]
        item_stock_out = item_transactions.filter(tx_type=InventoryTransaction.TxType.OUT).aggregate(
            total=Coalesce(
                Sum("quantity"),
                Value(0),
                output_field=DecimalField(max_digits=14, decimal_places=3),
            )
        )["total"]
        item_adjustments = item_transactions.filter(tx_type=InventoryTransaction.TxType.ADJUST).aggregate(
            total=Coalesce(
                Sum("quantity"),
                Value(0),
                output_field=DecimalField(max_digits=14, decimal_places=3),
            )
        )["total"]
        if item_stock_in - item_stock_out + item_adjustments <= rule.reorder_level:
            low_stock_items += 1

    context = {
        "today": today,
        "total_birds": total_birds,
        "active_batches_count": active_batches.count(),
        "active_houses_count": PoultryHouse.objects.filter(is_active=True).count(),
        "eggs_today": eggs_today,
        "eggs_in_stock_display": eggs_stock["available_display"],
        "feed_used_today": feed_used_today,
        "feed_stock": feed_stock,
        "deaths_today": MortalityRecord.objects.filter(
            record_date=today,
            status=ApprovalStatus.APPROVED,
        ).aggregate(total=Sum("number_dead"))["total"] or 0,
        "houses_cleaned_today": CleaningRecord.objects.filter(
            record_date=today,
            status=ApprovalStatus.APPROVED,
            house_cleaned=True,
        ).count(),
        "sickbay_cases": SicknessReport.objects.filter(isolated=True).count(),
        "pending_treatment_doses": TreatmentPlanItem.objects.filter(is_given=False).count(),
        "pending_vaccinations": VaccinationSchedule.objects.filter(
            status=VaccinationSchedule.Status.SCHEDULED
        ).count(),
        "low_stock_items": low_stock_items,
        "pending_approvals": pending_eggs + pending_feed + pending_cleaning + pending_mortality,
        "pending_orders": SaleInvoice.objects.filter(
            delivery_status=SaleInvoice.DeliveryStatus.PENDING
        ).exclude(status=SaleInvoice.Status.CANCELLED).count(),
        "outstanding_balance": ReceivableLedger.objects.aggregate(
            total=Coalesce(
                Sum("balance"),
                Value(0),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )["total"],
        "today_expenses": ExpenseTransaction.objects.filter(expense_date=today).aggregate(
            total=Coalesce(
                Sum("total_amount"),
                Value(0),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )["total"],
        "pending_salaries": SalaryPayment.objects.filter(
            status__in=[
                SalaryPayment.Status.PREPARED,
                SalaryPayment.Status.PART_PAID,
            ]
        ).count(),
        "active_alerts_count": active_alerts.count(),
        "supervisor_alerts_count": supervisor_alerts.count(),
        "urgent_alerts_count": active_alerts.filter(priority=Alert.Priority.URGENT).count(),
        "overdue_alerts_count": active_alerts.filter(due_date__lt=current_time).count(),
        "recent_supervisor_alerts": supervisor_alerts[:5],
        "upcoming_treatment_items": upcoming_treatment_items,
        "due_treatment_items_count": TreatmentPlanItem.objects.filter(
            is_given=False,
            scheduled_for__isnull=False,
            scheduled_for__lte=current_time,
        ).count(),
        "upcoming_vaccinations": upcoming_vaccinations,
        "due_vaccinations_count": VaccinationSchedule.objects.filter(
            status=VaccinationSchedule.Status.SCHEDULED,
            scheduled_for__lte=current_time,
        ).count(),
    }
    return render(request, 'dashboard.html', context)

def birds(request):
    batches = PoultryBatch.objects.select_related("house")

    # 🔍 filters
    status = request.GET.get("status")
    house = request.GET.get("house")
    cycle_filter = request.GET.get("cycle_filter", "").strip()

    if status:
        batches = batches.filter(status=status)

    if house:
        batches = batches.filter(house__pk=house)

    # also filter by breed if a breed filter is present
    breed_filter = request.GET.get("breed", "").strip()
    if breed_filter:
        batches = batches.filter(breed__icontains=breed_filter)

    batches = batches.order_by("-created_at")

    houses = PoultryHouse.objects.filter(is_active=True)

    batch_data = []

    for batch in batches:
        cycle_data = _batch_cycle_stage(batch)

        if cycle_filter == "laying" and not (
            batch.status == PoultryBatch.Status.ACTIVE and cycle_data["label"] != "Off laying"
        ):
            continue
        if cycle_filter == "off_layers" and not (
            batch.status == PoultryBatch.Status.ACTIVE and cycle_data["label"] == "Off laying"
        ):
            continue
        if cycle_filter == "closed_batch" and batch.status != PoultryBatch.Status.CLOSED:
            continue

        total_mortality = batch.mortality_records.aggregate(
            total=Sum("number_dead")
        )["total"] or 0

        total_eggs = batch.daily_production.aggregate(
            total=Sum("eggs_collected")
        )["total"] or 0

        current_sick_birds = SicknessReport.objects.filter(
            batch=batch,
            transferred_back_at__isnull=True,
        ).exclude(
            case_status=SicknessReport.CaseStatus.TREATMENT_COMPLETED
        ).aggregate(total=Sum("affected"))["total"] or 0

        total_sick_cases = SicknessReport.objects.filter(batch=batch).count()

        birds_sold = SaleItem.objects.filter(
            batch=batch
        ).filter(
            Q(product_name__icontains="bird") | Q(product_name__icontains="off layer")
        ).aggregate(total=Sum("quantity"))["total"] or 0

        assigned_workers = User.objects.filter(
            houses=batch.house,
            role__code="WORKER",
            is_active=True,
        ).order_by("first_name", "username")

        assigned_supervisors = User.objects.filter(
            houses=batch.house,
            role__code="SUPERVISOR",
            is_active=True,
        ).order_by("first_name", "username")

        current_birds = max(batch.initial_quantity - total_mortality - int(birds_sold), 0)

        # 🧠 performance logic
        if batch.initial_quantity > 0:
            mortality_rate = (total_mortality / batch.initial_quantity) * 100
        else:
            mortality_rate = 0

        # 🎯 classify health
        if mortality_rate < 3:
            health_status = "excellent"
        elif mortality_rate < 7:
            health_status = "warning"
        else:
            health_status = "critical"

        batch_data.append({
            "batch": batch,
            "current_birds": current_birds,
            "mortality_rate": round(mortality_rate, 2),
            "health_status": health_status,
            "total_eggs": total_eggs,
            "total_deaths": total_mortality,
            "current_sick_birds": current_sick_birds,
            "total_sick_cases": total_sick_cases,
            "birds_sold": birds_sold,
            "cycle": cycle_data,
            "assigned_workers": assigned_workers,
            "assigned_supervisors": assigned_supervisors,
        })

    page_obj, querystring = paginate(request, batch_data, per_page=12)

    context = {
        "batch_data": page_obj,
        "page_obj": page_obj,
        "querystring": querystring,
        "houses": PoultryHouse.objects.filter(is_active=True),
        "breeds": PoultryBatch.objects.exclude(breed="").values_list("breed", flat=True).distinct().order_by("breed"),
        "selected_status": status,
        "selected_house": house,
        "selected_cycle_filter": cycle_filter,
        "selected_breed": breed_filter,
    }
    return render(request, 'birds.html', context)


def _get_worker_active_batches(user):
    return PoultryBatch.objects.filter(
        house__in=user.houses.all(),
        status=PoultryBatch.Status.ACTIVE,
    ).select_related("house").order_by("house__house_code", "batch_code")


def _selected_or_only(queryset, pk):
    if pk:
        return queryset.filter(pk=pk).first()
    if queryset.count() == 1:
        return queryset.first()
    return None


def _role_code(user) -> str:
    return (getattr(user.role, "code", "") or "").upper()


def _scope_to_supervisor_houses(queryset, user, house_lookup):
    if _role_code(user) == "SUPERVISOR":
        return queryset.filter(**{f"{house_lookup}__in": user.houses.all()})
    return queryset


def _mixtures_for_worker_batches(batches):
    return (
        FeedMixture.objects.filter(
            mix_date=date.today(),
        )
        .filter(
            Q(allocations__batch__in=batches)
            | Q(
                allocations__batch__isnull=True,
                allocations__house__in=batches.values("house"),
            )
        )
        .select_related("mixed_by")
        .prefetch_related("allocations__house", "allocations__batch", "ingredients")
        .distinct()
        .order_by("-created_at")
    )


def _stock_for_item(item):
    transactions = InventoryTransaction.objects.filter(item=item)
    stock_in = transactions.filter(tx_type=InventoryTransaction.TxType.IN_).aggregate(total=Sum("quantity"))["total"] or Decimal("0.000")
    stock_out = transactions.filter(tx_type=InventoryTransaction.TxType.OUT).aggregate(total=Sum("quantity"))["total"] or Decimal("0.000")
    adjustments = transactions.filter(tx_type=InventoryTransaction.TxType.ADJUST).aggregate(total=Sum("quantity"))["total"] or Decimal("0.000")
    return stock_in - stock_out + adjustments


def scale_formula_ingredients(formula, target_weight_kg):
    """Return persisted-ready ingredient lines scaled to an exact two-decimal total."""
    target_weight_kg = Decimal(target_weight_kg).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    reference_weight = Decimal(formula.reference_weight_kg)
    if target_weight_kg <= 0 or reference_weight <= 0:
        return []

    formula_lines = list(formula.ingredients.select_related("item").order_by("sort_order", "formula_ingredient_id"))
    if not formula_lines:
        return []

    scale = target_weight_kg / reference_weight
    scaled = [
        {
            "item": line.item,
            "feed_type": FeedRecord.FeedType.OTHER,
            "ingredient_name": line.item.name,
            "quantity_kg": (line.quantity_kg * scale).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        }
        for line in formula_lines
    ]
    scaled = [line for line in scaled if line["quantity_kg"] > 0]
    if not scaled:
        return []

    scaled_total = sum((line["quantity_kg"] for line in scaled), Decimal("0.00"))
    scaled[-1]["quantity_kg"] += target_weight_kg - scaled_total
    return scaled


def _batch_formula_stage(batch):
    return flock_stage_for_age_days(batch.current_age_days)


def _stage_label(stage):
    return dict(FlockStage.choices).get(stage, stage)


def _build_investor_builder_data(start_date, end_date, group_by):
    """Build the investor dashboard's ad-hoc analysis catalogue."""

    def number(value):
        return float(value or 0)

    def decimal_value(value):
        return Decimal(str(value or 0))

    def pct(numerator, denominator):
        denominator = decimal_value(denominator)
        if denominator == 0:
            return 0
        return number(decimal_value(numerator) / denominator * Decimal("100"))

    def point(label, value):
        return {"label": str(label or "Unassigned"), "value": number(value)}

    def top_points(items, max_items=14):
        points = [point(label, value) for label, value in items if label is not None]
        points.sort(key=lambda item: abs(item["value"]), reverse=True)
        if len(points) <= max_items:
            return points
        shown = points[:max_items]
        other_total = sum(item["value"] for item in points[max_items:])
        if other_total:
            shown.append(point("Other", other_total))
        return shown

    def period_label(value):
        value_date = value.date() if isinstance(value, datetime) else value
        if group_by == "week":
            week_start = value_date - timedelta(days=value_date.weekday())
            return f"Week of {week_start.strftime('%d %b %Y')}"
        if group_by == "day":
            return value_date.strftime("%d %b %Y")
        return value_date.strftime("%b %Y")

    def period_zero_map():
        grouped = {}
        current = start_date
        while current <= end_date:
            grouped.setdefault(period_label(current), Decimal("0"))
            current += timedelta(days=1)
        return grouped

    def period_sum_points(queryset, date_field, value_field):
        grouped = period_zero_map()
        for row in queryset.values(date_field).annotate(total=Sum(value_field)).order_by(date_field):
            row_date = row.get(date_field)
            if row_date:
                grouped[period_label(row_date)] = grouped.get(period_label(row_date), Decimal("0")) + decimal_value(row["total"])
        return [point(label, value) for label, value in grouped.items()]

    def period_count_points(queryset, date_field):
        grouped = period_zero_map()
        for row in queryset.values(date_field).annotate(total=Count("pk")).order_by(date_field):
            row_date = row.get(date_field)
            if row_date:
                grouped[period_label(row_date)] = grouped.get(period_label(row_date), Decimal("0")) + decimal_value(row["total"])
        return [point(label, value) for label, value in grouped.items()]

    def period_avg_points(queryset, date_field, value_field):
        sums = period_zero_map()
        counts = {label: 0 for label in sums}
        for row in queryset.exclude(**{f"{value_field}__isnull": True}).values(date_field, value_field).order_by(date_field):
            row_date = row.get(date_field)
            if not row_date:
                continue
            label = period_label(row_date)
            sums[label] = sums.get(label, Decimal("0")) + decimal_value(row[value_field])
            counts[label] = counts.get(label, 0) + 1
        return [
            point(label, (value / counts[label]) if counts.get(label) else 0)
            for label, value in sums.items()
        ]

    def sum_points(queryset, group_fields, value_field, label_func, max_items=14):
        group_fields = [group_fields] if isinstance(group_fields, str) else list(group_fields)
        rows = queryset.values(*group_fields).annotate(total=Sum(value_field)).order_by()
        return top_points(((label_func(row), row["total"] or 0) for row in rows), max_items=max_items)

    def count_points(queryset, group_fields, label_func, max_items=14):
        group_fields = [group_fields] if isinstance(group_fields, str) else list(group_fields)
        rows = queryset.values(*group_fields).annotate(total=Count("pk")).order_by()
        return top_points(((label_func(row), row["total"] or 0) for row in rows), max_items=max_items)

    def avg_points(queryset, group_fields, value_field, label_func, max_items=14):
        group_fields = [group_fields] if isinstance(group_fields, str) else list(group_fields)
        rows = queryset.exclude(**{f"{value_field}__isnull": True}).values(*group_fields).annotate(total=Avg(value_field)).order_by()
        return top_points(((label_func(row), row["total"] or 0) for row in rows), max_items=max_items)

    def points_map(points):
        return {item["label"]: decimal_value(item["value"]) for item in points}

    def combine_points(first, second, func):
        first_map = points_map(first)
        second_map = points_map(second)
        labels = []
        for item in first + second:
            if item["label"] not in labels:
                labels.append(item["label"])
        return [point(label, func(first_map.get(label, Decimal("0")), second_map.get(label, Decimal("0")))) for label in labels]

    def ratio_points(numerator_points, denominator_points, multiplier=100):
        return combine_points(
            numerator_points,
            denominator_points,
            lambda numerator, denominator: (numerator / denominator * Decimal(str(multiplier))) if denominator else Decimal("0"),
        )

    def ratio_points_static_denominator(numerator_points, denominator, multiplier=100):
        denominator = decimal_value(denominator)
        return [
            point(item["label"], (decimal_value(item["value"]) / denominator * Decimal(str(multiplier))) if denominator else 0)
            for item in numerator_points
        ]

    def scale_points(points, multiplier):
        multiplier = decimal_value(multiplier)
        return [point(item["label"], decimal_value(item["value"]) * multiplier) for item in points]

    def house_label(row, prefix="batch__house"):
        code = row.get(f"{prefix}__house_code")
        name = row.get(f"{prefix}__name")
        if code and name and code != name:
            return f"{code} - {name}"
        return name or code or row.get("house") or "Unassigned"

    def batch_label(row, prefix="batch"):
        return row.get(f"{prefix}__batch_code") or "Unassigned"

    def user_label(row, prefix):
        full_name = f"{row.get(f'{prefix}__first_name') or ''} {row.get(f'{prefix}__last_name') or ''}".strip()
        return full_name or row.get(f"{prefix}__username") or "Unassigned"

    def selected_period_points(value):
        return [point("Selected period", value)]

    def current_snapshot_points(value):
        return [point("Current snapshot", value)]

    def salary_period_points(queryset):
        grouped = period_zero_map()
        for row in queryset.values("period_month").annotate(total=Sum("amount")).order_by("period_month"):
            raw_month = row.get("period_month")
            try:
                month_date = date.fromisoformat(f"{raw_month}-01")
            except (TypeError, ValueError):
                continue
            grouped[period_label(month_date)] = grouped.get(period_label(month_date), Decimal("0")) + decimal_value(row["total"])
        return [point(label, value) for label, value in grouped.items()]

    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
    if is_naive(start_datetime):
        start_datetime = make_aware(start_datetime)
    if is_naive(end_datetime):
        end_datetime = make_aware(end_datetime)

    sales_qs = SaleInvoice.objects.exclude(status=SaleInvoice.Status.CANCELLED).filter(invoice_date__range=(start_date, end_date))
    sale_items_qs = SaleItem.objects.select_related("invoice", "invoice__customer", "batch__house").filter(invoice__in=sales_qs)
    manure_sale_items_qs = sale_items_qs.filter(product_name__icontains="manure")
    payments_qs = CustomerPayment.objects.select_related("customer", "invoice").filter(payment_date__range=(start_date, end_date))
    expenses_qs = ExpenseTransaction.objects.exclude(status=ExpenseTransaction.Status.REJECTED).select_related("category").filter(expense_date__range=(start_date, end_date))
    capital_qs = InvestorCapitalTransaction.objects.filter(transaction_date__range=(start_date, end_date))
    eggs_qs = egg_collection.objects.select_related("batch__house").filter(collection_date__range=(start_date, end_date), status=ApprovalStatus.APPROVED)
    mortality_qs = MortalityRecord.objects.select_related("batch__house", "cause").filter(record_date__range=(start_date, end_date), status=ApprovalStatus.APPROVED)
    feed_qs = FeedRecord.objects.select_related("batch__house").filter(record_date__range=(start_date, end_date), status=ApprovalStatus.APPROVED)
    feed_mixtures_qs = FeedMixture.objects.filter(mix_date__range=(start_date, end_date))
    cleaning_qs = CleaningRecord.objects.select_related("batch__house").filter(record_date__range=(start_date, end_date), status=ApprovalStatus.APPROVED)
    sickness_qs = SicknessReport.objects.select_related("house_ref", "batch__house").filter(date__range=(start_date, end_date))
    health_event_qs = HealthEvent.objects.select_related("batch__house").filter(event_date__range=(start_date, end_date))
    treatment_qs = TreatmentPlanItem.objects.select_related("sickness_report", "created_by").filter(
        scheduled_for__gte=start_datetime,
        scheduled_for__lt=end_datetime,
    )
    vaccination_qs = VaccinationSchedule.objects.select_related("house_ref", "batch__house").filter(
        scheduled_for__gte=start_datetime,
        scheduled_for__lt=end_datetime,
    )
    sickbay_cleaning_qs = SickbayCleaningRecord.objects.select_related("sickness_report").filter(record_date__range=(start_date, end_date))
    inventory_qs = InventoryTransaction.objects.select_related("item", "item__category", "store").filter(tx_date__range=(start_date, end_date))
    attendance_qs = Attendance.objects.select_related("worker").filter(work_date__range=(start_date, end_date))
    wages_qs = WagePayment.objects.select_related("worker", "batch__house").filter(payment_date__range=(start_date, end_date))
    salary_qs = SalaryPayment.objects.select_related("employee").filter(
        period_month__gte=start_date.strftime("%Y-%m"),
        period_month__lte=end_date.strftime("%Y-%m"),
    )

    total_revenue = sales_qs.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")
    cash_received = payments_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    total_expenses = expenses_qs.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")
    total_profit = total_revenue - total_expenses
    outstanding_receivables = ReceivableLedger.objects.aggregate(total=Sum("balance"))["total"] or Decimal("0")
    total_eggs = eggs_qs.aggregate(total=Sum("eggs_collected"))["total"] or 0
    rejected_eggs = eggs_qs.aggregate(total=Sum("eggs_rejected"))["total"] or 0
    net_eggs = max((total_eggs or 0) - (rejected_eggs or 0), 0)
    total_feed = feed_qs.aggregate(total=Sum("quantity_kg"))["total"] or Decimal("0")
    total_deaths = mortality_qs.aggregate(total=Sum("number_dead"))["total"] or 0
    capital_invested = capital_qs.filter(
        transaction_type__in=[
            InvestorCapitalTransaction.TransactionType.STARTUP,
            InvestorCapitalTransaction.TransactionType.ADDITION,
        ]
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    owner_withdrawals = capital_qs.filter(
        transaction_type=InvestorCapitalTransaction.TransactionType.WITHDRAWAL
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    active_batches = PoultryBatch.objects.filter(status=PoultryBatch.Status.ACTIVE).select_related("house")
    current_birds = 0
    house_current = {}
    house_initial = {}
    house_capacity = {}
    house_active_batches = {}
    batch_current = []
    batch_initial = []
    batch_purchase_cost = []
    for batch in active_batches:
        approved_deaths = batch.mortality_records.filter(status=ApprovalStatus.APPROVED).aggregate(total=Sum("number_dead"))["total"] or 0
        birds_sold = SaleItem.objects.filter(batch=batch).filter(
            Q(product_name__icontains="bird") | Q(product_name__icontains="off layer")
        ).aggregate(total=Sum("quantity"))["total"] or 0
        live_birds = max(batch.initial_quantity - int(approved_deaths) - int(birds_sold), 0)
        current_birds += live_birds
        label = f"{batch.house.house_code} - {batch.house.name}" if batch.house.name and batch.house.house_code != batch.house.name else (batch.house.name or batch.house.house_code)
        house_current[label] = house_current.get(label, 0) + live_birds
        house_initial[label] = house_initial.get(label, 0) + batch.initial_quantity
        house_capacity[label] = batch.house.capacity or 0
        house_active_batches[label] = house_active_batches.get(label, 0) + 1
        batch_current.append((batch.batch_code, live_birds))
        batch_initial.append((batch.batch_code, batch.initial_quantity))
        batch_purchase_cost.append((batch.batch_code, batch.amount_paid or 0))

    all_active_houses = PoultryHouse.objects.filter(is_active=True)
    for house in all_active_houses:
        label = f"{house.house_code} - {house.name}" if house.name and house.house_code != house.name else (house.name or house.house_code)
        house_capacity.setdefault(label, house.capacity or 0)
        house_current.setdefault(label, 0)
        house_initial.setdefault(label, 0)

    total_capacity = sum(house_capacity.values())
    house_utilization = [
        (label, pct(house_current.get(label, 0), capacity))
        for label, capacity in house_capacity.items()
    ]

    inventory_balance_by_item = {}
    inventory_balance_by_store = {}
    inventory_balance_by_store_item = {}
    for tx in InventoryTransaction.objects.select_related("item", "store").filter(tx_date__lte=end_date).order_by("tx_date", "tx_id"):
        sign = Decimal("-1") if tx.tx_type == InventoryTransaction.TxType.OUT else Decimal("1")
        quantity = decimal_value(tx.quantity) * sign
        item_row = inventory_balance_by_item.setdefault(
            tx.item_id,
            {"label": tx.item.name, "quantity": Decimal("0"), "unit_price": Decimal("0")},
        )
        store_row = inventory_balance_by_store.setdefault(
            tx.store_id,
            {"label": tx.store.name, "quantity": Decimal("0"), "unit_price": Decimal("0")},
        )
        store_item_row = inventory_balance_by_store_item.setdefault(
            (tx.store_id, tx.item_id),
            {"label": f"{tx.store.name} / {tx.item.name}", "store_label": tx.store.name, "quantity": Decimal("0"), "unit_price": Decimal("0")},
        )
        for row in (item_row, store_row, store_item_row):
            row["quantity"] += quantity
            if tx.unit_price is not None:
                row["unit_price"] = tx.unit_price

    inventory_balance_item_points = top_points((row["label"], max(row["quantity"], Decimal("0"))) for row in inventory_balance_by_item.values())
    inventory_balance_store_points = top_points((row["label"], max(row["quantity"], Decimal("0"))) for row in inventory_balance_by_store.values())
    inventory_value_item_points = top_points(
        (row["label"], max(row["quantity"], Decimal("0")) * row["unit_price"])
        for row in inventory_balance_by_item.values()
    )
    inventory_value_by_store = {}
    for row in inventory_balance_by_store_item.values():
        inventory_value_by_store[row["store_label"]] = inventory_value_by_store.get(row["store_label"], Decimal("0")) + (
            max(row["quantity"], Decimal("0")) * row["unit_price"]
        )
    inventory_value_store_points = top_points(inventory_value_by_store.items())
    inventory_balance_total = sum(max(row["quantity"], Decimal("0")) for row in inventory_balance_by_item.values())
    inventory_value_total = sum(max(row["quantity"], Decimal("0")) * row["unit_price"] for row in inventory_balance_by_item.values())

    low_stock_items = []
    for rule in ReorderRule.objects.select_related("store", "item").filter(alerts_enabled=True):
        stock_row = inventory_balance_by_store_item.get((rule.store_id, rule.item_id), {"quantity": Decimal("0")})
        if stock_row["quantity"] <= rule.reorder_level:
            low_stock_items.append((f"{rule.store.name} / {rule.item.name}", 1))

    stock_in_qs = inventory_qs.filter(tx_type=InventoryTransaction.TxType.IN_)
    stock_out_qs = inventory_qs.filter(tx_type=InventoryTransaction.TxType.OUT)
    stock_adjust_qs = inventory_qs.filter(tx_type=InventoryTransaction.TxType.ADJUST)
    feed_stock_out_qs = stock_out_qs.filter(item__category__code__icontains="FEED")
    feed_expense_qs = expenses_qs.filter(
        Q(category__code__icontains="FEED") | Q(category__name__icontains="feed") | Q(item_name__icontains="feed")
    )
    labour_expense_qs = expenses_qs.filter(
        Q(category__code__icontains="LAB") | Q(category__name__icontains="labour") | Q(category__name__icontains="labor")
    )
    expense_allocations_qs = ExpenseAllocation.objects.select_related("batch__house", "expense").filter(expense__in=expenses_qs)
    batch_sale_items_qs = sale_items_qs.filter(batch__isnull=False)

    sales_period = period_sum_points(sales_qs, "invoice_date", "total_amount")
    expense_period = period_sum_points(expenses_qs, "expense_date", "total_amount")
    cash_period = period_sum_points(payments_qs, "payment_date", "amount")
    feed_cost_period = period_sum_points(feed_expense_qs, "expense_date", "total_amount")
    labour_cost_period = combine_points(
        combine_points(
            period_sum_points(wages_qs, "payment_date", "amount"),
            salary_period_points(salary_qs),
            lambda wages, salaries: wages + salaries,
        ),
        period_sum_points(labour_expense_qs, "expense_date", "total_amount"),
        lambda payroll, expenses: payroll + expenses,
    )
    profit_period = combine_points(sales_period, expense_period, lambda revenue, expenses: revenue - expenses)
    profit_margin_period = ratio_points(profit_period, sales_period)
    expense_ratio_period = ratio_points(expense_period, sales_period)
    collection_rate_period = ratio_points(cash_period, sales_period)
    eggs_period = period_sum_points(eggs_qs, "collection_date", "eggs_collected")
    rejected_period = period_sum_points(eggs_qs, "collection_date", "eggs_rejected")
    net_eggs_period = combine_points(eggs_period, rejected_period, lambda collected, rejected: max(collected - rejected, Decimal("0")))
    feed_period = period_sum_points(feed_qs, "record_date", "quantity_kg")
    deaths_period = period_sum_points(mortality_qs, "record_date", "number_dead")
    feed_per_egg_period = ratio_points(feed_period, eggs_period, multiplier=1)
    rejection_rate_period = ratio_points(rejected_period, combine_points(eggs_period, rejected_period, lambda collected, rejected: collected + rejected))
    mortality_rate_period = ratio_points_static_denominator(deaths_period, current_birds + total_deaths)
    cost_per_egg_period = ratio_points(expense_period, net_eggs_period, multiplier=1)
    feed_cost_per_egg_period = ratio_points(feed_cost_period, net_eggs_period, multiplier=1)

    eggs_house = sum_points(eggs_qs, ["batch__house__house_code", "batch__house__name"], "eggs_collected", house_label)
    eggs_batch = sum_points(eggs_qs, "batch__batch_code", "eggs_collected", batch_label)
    rejected_house = sum_points(eggs_qs, ["batch__house__house_code", "batch__house__name"], "eggs_rejected", house_label)
    rejected_batch = sum_points(eggs_qs, "batch__batch_code", "eggs_rejected", batch_label)
    feed_house = sum_points(feed_qs, ["batch__house__house_code", "batch__house__name"], "quantity_kg", house_label)
    feed_batch = sum_points(feed_qs, "batch__batch_code", "quantity_kg", batch_label)
    deaths_house = sum_points(mortality_qs, ["batch__house__house_code", "batch__house__name"], "number_dead", house_label)
    deaths_batch = sum_points(mortality_qs, "batch__batch_code", "number_dead", batch_label)

    live_birds_house = top_points(house_current.items())
    live_birds_batch = top_points(batch_current)
    capacity_house = top_points(house_capacity.items())
    active_batches_house = top_points(house_active_batches.items())
    feed_cost_total = feed_expense_qs.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")
    labour_cost_total = (
        (wages_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0"))
        + (salary_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0"))
        + (labour_expense_qs.aggregate(total=Sum("total_amount"))["total"] or Decimal("0"))
    )
    egg_sales_revenue = sale_items_qs.filter(product_name__icontains="egg").aggregate(total=Sum("line_total"))["total"] or Decimal("0")
    revenue_per_egg = (egg_sales_revenue / decimal_value(net_eggs)) if net_eggs else Decimal("0")
    cost_per_egg = (total_expenses / decimal_value(net_eggs)) if net_eggs else Decimal("0")
    feed_cost_per_egg = (feed_cost_total / decimal_value(net_eggs)) if net_eggs else Decimal("0")
    rejected_egg_loss = decimal_value(rejected_eggs) * revenue_per_egg
    stocked_birds = sum(value for _, value in batch_initial)
    batch_purchase_total = sum(decimal_value(value) for _, value in batch_purchase_cost)
    average_bird_cost = (batch_purchase_total / decimal_value(stocked_birds)) if stocked_birds else Decimal("0")
    mortality_loss_estimate = decimal_value(total_deaths) * average_bird_cost
    batch_revenue_points = sum_points(batch_sale_items_qs, "batch__batch_code", "line_total", batch_label)
    batch_expense_points = sum_points(expense_allocations_qs.filter(batch__isnull=False), "batch__batch_code", "amount_allocated", batch_label)
    batch_profit_points = combine_points(batch_revenue_points, batch_expense_points, lambda revenue, expense: revenue - expense)
    house_revenue_points = sum_points(batch_sale_items_qs, ["batch__house__house_code", "batch__house__name"], "line_total", house_label)
    house_expense_points = sum_points(expense_allocations_qs.filter(batch__isnull=False), ["batch__house__house_code", "batch__house__name"], "amount_allocated", house_label)
    house_profit_points = combine_points(house_revenue_points, house_expense_points, lambda revenue, expense: revenue - expense)

    payment_method_labels = dict(CustomerPayment.Method.choices)
    capital_type_labels = dict(InvestorCapitalTransaction.TransactionType.choices)
    feed_type_labels = dict(FeedRecord.FeedType.choices)
    health_event_labels = dict(HealthEvent.EventType.choices)
    attendance_status_labels = dict(Attendance.AttendanceStatus.choices)
    vaccination_status_labels = dict(VaccinationSchedule.Status.choices)
    sickness_status_labels = dict(SicknessReport.CaseStatus.choices)

    indicators = []

    def add_indicator(key, label, theme, unit, value_format, summary, dimensions, icon, featured=False):
        indicators.append({
            "key": key,
            "label": label,
            "theme": theme,
            "unit": unit,
            "format": value_format,
            "summary": number(summary),
            "dimensions": {dimension: values for dimension, values in dimensions.items() if values},
            "icon": icon,
            "featured": featured,
        })

    add_indicator("sales_revenue", "Sales revenue", "sales", "UGX", "currency", total_revenue, {
        "period": sales_period,
        "product": sum_points(sale_items_qs, "product_name", "line_total", lambda row: row.get("product_name") or "Unspecified"),
        "customer": sum_points(sales_qs, "customer__name", "total_amount", lambda row: row.get("customer__name") or "Walk-in"),
        "house": sum_points(sale_items_qs, ["batch__house__house_code", "batch__house__name"], "line_total", house_label),
        "batch": sum_points(sale_items_qs, "batch__batch_code", "line_total", batch_label),
    }, "bi-graph-up-arrow", featured=True)
    add_indicator("cash_received", "Cash received", "sales", "UGX", "currency", cash_received, {
        "period": cash_period,
        "customer": sum_points(payments_qs, "customer__name", "amount", lambda row: row.get("customer__name") or "Walk-in"),
        "payment_method": sum_points(payments_qs, "method", "amount", lambda row: payment_method_labels.get(row.get("method"), row.get("method") or "Unknown")),
    }, "bi-cash-stack", featured=True)
    add_indicator("total_expenses", "Total expenses", "returns", "UGX", "currency", total_expenses, {
        "period": expense_period,
        "expense_category": sum_points(expenses_qs, "category__name", "total_amount", lambda row: row.get("category__name") or "Uncategorised"),
        "supplier": sum_points(expenses_qs, "supplier_name", "total_amount", lambda row: row.get("supplier_name") or "No supplier"),
    }, "bi-receipt", featured=True)
    add_indicator("profit", "Profit", "returns", "UGX", "currency", total_profit, {"period": profit_period}, "bi-bank", featured=True)
    add_indicator("profit_margin", "Profit margin", "returns", "%", "percent", pct(total_profit, total_revenue), {"period": profit_margin_period}, "bi-percent", featured=True)
    add_indicator("expense_ratio", "Expense ratio", "returns", "%", "percent", pct(total_expenses, total_revenue), {"period": expense_ratio_period}, "bi-pie-chart")
    add_indicator("feed_cost", "Feed cost", "feed", "UGX", "currency", feed_cost_total, {
        "period": feed_cost_period,
        "expense_category": sum_points(feed_expense_qs, "category__name", "total_amount", lambda row: row.get("category__name") or "Feed"),
        "supplier": sum_points(feed_expense_qs, "supplier_name", "total_amount", lambda row: row.get("supplier_name") or "No supplier"),
    }, "bi-bag-check", featured=True)
    add_indicator("labour_cost", "Labour cost", "workforce", "UGX", "currency", labour_cost_total, {
        "period": labour_cost_period,
        "staff": combine_points(
            sum_points(wages_qs, "worker__full_name", "amount", lambda row: row.get("worker__full_name") or "Wage labour"),
            sum_points(salary_qs, ["employee__first_name", "employee__last_name", "employee__username"], "amount", lambda row: user_label(row, "employee")),
            lambda wages, salary: wages + salary,
        ),
        "expense_category": sum_points(labour_expense_qs, "category__name", "total_amount", lambda row: row.get("category__name") or "Labour"),
    }, "bi-person-workspace", featured=True)
    add_indicator("cost_per_egg", "Cost per saleable egg", "feed", "UGX", "currency", cost_per_egg, {
        "period": cost_per_egg_period,
        "house": ratio_points(sum_points(expense_allocations_qs.filter(batch__isnull=False), ["batch__house__house_code", "batch__house__name"], "amount_allocated", house_label), combine_points(eggs_house, rejected_house, lambda eggs, rejected: max(eggs - rejected, Decimal("0"))), multiplier=1),
        "batch": ratio_points(batch_expense_points, combine_points(eggs_batch, rejected_batch, lambda eggs, rejected: max(eggs - rejected, Decimal("0"))), multiplier=1),
    }, "bi-coin", featured=True)
    add_indicator("feed_cost_per_egg", "Feed cost per saleable egg", "feed", "UGX", "currency", feed_cost_per_egg, {
        "period": feed_cost_per_egg_period,
        "house": ratio_points(sum_points(feed_expense_qs.filter(allocations__batch__isnull=False), ["allocations__batch__house__house_code", "allocations__batch__house__name"], "allocations__amount_allocated", lambda row: house_label(row, "allocations__batch__house")), combine_points(eggs_house, rejected_house, lambda eggs, rejected: max(eggs - rejected, Decimal("0"))), multiplier=1),
        "batch": ratio_points(sum_points(feed_expense_qs.filter(allocations__batch__isnull=False), "allocations__batch__batch_code", "allocations__amount_allocated", lambda row: row.get("allocations__batch__batch_code") or "Unassigned"), combine_points(eggs_batch, rejected_batch, lambda eggs, rejected: max(eggs - rejected, Decimal("0"))), multiplier=1),
    }, "bi-bag-heart")
    add_indicator("revenue_per_egg", "Egg sales revenue per saleable egg", "sales", "UGX", "currency", revenue_per_egg, {
        "period": ratio_points(period_sum_points(sale_items_qs.filter(product_name__icontains="egg"), "invoice__invoice_date", "line_total"), net_eggs_period, multiplier=1),
        "house": ratio_points(sum_points(sale_items_qs.filter(product_name__icontains="egg"), ["batch__house__house_code", "batch__house__name"], "line_total", house_label), combine_points(eggs_house, rejected_house, lambda eggs, rejected: max(eggs - rejected, Decimal("0"))), multiplier=1),
        "batch": ratio_points(sum_points(sale_items_qs.filter(product_name__icontains="egg"), "batch__batch_code", "line_total", batch_label), combine_points(eggs_batch, rejected_batch, lambda eggs, rejected: max(eggs - rejected, Decimal("0"))), multiplier=1),
    }, "bi-cash-stack")
    add_indicator("rejected_egg_loss", "Rejected egg revenue loss estimate", "production", "UGX", "currency", rejected_egg_loss, {
        "period": scale_points(rejected_period, revenue_per_egg),
        "house": scale_points(rejected_house, revenue_per_egg),
        "batch": scale_points(rejected_batch, revenue_per_egg),
    }, "bi-egg-fried", featured=True)
    add_indicator("mortality_loss_estimate", "Mortality loss estimate", "health", "UGX", "currency", mortality_loss_estimate, {
        "period": scale_points(deaths_period, average_bird_cost),
        "house": scale_points(deaths_house, average_bird_cost),
        "batch": scale_points(deaths_batch, average_bird_cost),
    }, "bi-heartbreak")
    add_indicator("batch_revenue", "Batch revenue", "production", "UGX", "currency", sum(decimal_value(item["value"]) for item in batch_revenue_points), {
        "batch": batch_revenue_points,
        "house": house_revenue_points,
        "period": period_sum_points(batch_sale_items_qs, "invoice__invoice_date", "line_total"),
    }, "bi-box-arrow-up-right", featured=True)
    add_indicator("batch_allocated_expenses", "Batch allocated expenses", "production", "UGX", "currency", sum(decimal_value(item["value"]) for item in batch_expense_points), {
        "batch": batch_expense_points,
        "house": house_expense_points,
        "period": period_sum_points(expense_allocations_qs, "expense__expense_date", "amount_allocated"),
    }, "bi-box-arrow-in-down-right", featured=True)
    add_indicator("batch_profit", "Batch profit", "production", "UGX", "currency", sum(decimal_value(item["value"]) for item in batch_profit_points), {
        "batch": batch_profit_points,
        "house": house_profit_points,
        "period": combine_points(
            period_sum_points(batch_sale_items_qs, "invoice__invoice_date", "line_total"),
            period_sum_points(expense_allocations_qs, "expense__expense_date", "amount_allocated"),
            lambda revenue, expenses: revenue - expenses,
        ),
    }, "bi-graph-up-arrow", featured=True)
    add_indicator("collection_rate", "Collection rate", "sales", "%", "percent", pct(cash_received, total_revenue), {"period": collection_rate_period}, "bi-wallet2", featured=True)
    add_indicator("receivables_outstanding", "Receivables outstanding", "sales", "UGX", "currency", outstanding_receivables, {
        "customer": sum_points(ReceivableLedger.objects.select_related("invoice__customer"), "invoice__customer__name", "balance", lambda row: row.get("invoice__customer__name") or "Unknown"),
        "period": selected_period_points(outstanding_receivables),
    }, "bi-file-earmark-text")
    add_indicator("invoices_issued", "Invoices issued", "sales", "records", "number", sales_qs.count(), {
        "period": period_count_points(sales_qs, "invoice_date"),
        "customer": count_points(sales_qs, "customer__name", lambda row: row.get("customer__name") or "Walk-in"),
    }, "bi-file-earmark-check")
    add_indicator("average_invoice_value", "Average invoice value", "sales", "UGX", "currency", (total_revenue / sales_qs.count()) if sales_qs.count() else 0, {
        "period": ratio_points(sales_period, period_count_points(sales_qs, "invoice_date"), multiplier=1),
        "customer": avg_points(sales_qs, "customer__name", "total_amount", lambda row: row.get("customer__name") or "Walk-in"),
    }, "bi-calculator")
    add_indicator("manure_sales", "Manure sales", "sales", "UGX", "currency", manure_sale_items_qs.aggregate(total=Sum("line_total"))["total"] or 0, {
        "period": period_sum_points(manure_sale_items_qs, "invoice__invoice_date", "line_total"),
        "customer": sum_points(manure_sale_items_qs, "invoice__customer__name", "line_total", lambda row: row.get("invoice__customer__name") or "Walk-in"),
        "house": sum_points(manure_sale_items_qs, ["batch__house__house_code", "batch__house__name"], "line_total", house_label),
        "batch": sum_points(manure_sale_items_qs, "batch__batch_code", "line_total", batch_label),
    }, "bi-flower1")
    add_indicator("manure_quantity_sold", "Manure quantity sold", "sales", "units", "decimal", manure_sale_items_qs.aggregate(total=Sum("quantity"))["total"] or 0, {
        "period": period_sum_points(manure_sale_items_qs, "invoice__invoice_date", "quantity"),
        "customer": sum_points(manure_sale_items_qs, "invoice__customer__name", "quantity", lambda row: row.get("invoice__customer__name") or "Walk-in"),
        "house": sum_points(manure_sale_items_qs, ["batch__house__house_code", "batch__house__name"], "quantity", house_label),
        "batch": sum_points(manure_sale_items_qs, "batch__batch_code", "quantity", batch_label),
    }, "bi-basket")
    add_indicator("capital_invested", "Capital invested", "returns", "UGX", "currency", capital_invested, {
        "period": period_sum_points(capital_qs.exclude(transaction_type=InvestorCapitalTransaction.TransactionType.WITHDRAWAL), "transaction_date", "amount"),
        "capital_type": sum_points(capital_qs, "transaction_type", "amount", lambda row: capital_type_labels.get(row.get("transaction_type"), row.get("transaction_type") or "Unknown")),
    }, "bi-safe")
    add_indicator("owner_withdrawals", "Owner withdrawals", "returns", "UGX", "currency", owner_withdrawals, {
        "period": period_sum_points(capital_qs.filter(transaction_type=InvestorCapitalTransaction.TransactionType.WITHDRAWAL), "transaction_date", "amount"),
        "capital_type": sum_points(capital_qs.filter(transaction_type=InvestorCapitalTransaction.TransactionType.WITHDRAWAL), "transaction_type", "amount", lambda row: capital_type_labels.get(row.get("transaction_type"), row.get("transaction_type") or "Unknown")),
    }, "bi-arrow-down-up")

    add_indicator("eggs_collected", "Eggs collected", "production", "eggs", "number", total_eggs, {"period": eggs_period, "house": eggs_house, "batch": eggs_batch}, "bi-egg", featured=True)
    add_indicator("rejected_eggs", "Broken / rejected eggs", "production", "eggs", "number", rejected_eggs, {"period": rejected_period, "house": rejected_house, "batch": rejected_batch}, "bi-x-circle")
    add_indicator("net_eggs", "Net eggs", "production", "eggs", "number", net_eggs, {
        "period": net_eggs_period,
        "house": combine_points(eggs_house, rejected_house, lambda collected, rejected: max(collected - rejected, Decimal("0"))),
        "batch": combine_points(eggs_batch, rejected_batch, lambda collected, rejected: max(collected - rejected, Decimal("0"))),
    }, "bi-check2-circle", featured=True)
    add_indicator("egg_rejection_rate", "Egg rejection rate", "production", "%", "percent", pct(rejected_eggs, decimal_value(total_eggs) + decimal_value(rejected_eggs)), {
        "period": rejection_rate_period,
        "house": ratio_points(rejected_house, combine_points(eggs_house, rejected_house, lambda collected, rejected: collected + rejected)),
        "batch": ratio_points(rejected_batch, combine_points(eggs_batch, rejected_batch, lambda collected, rejected: collected + rejected)),
    }, "bi-percent")
    add_indicator("average_egg_weight", "Average egg weight", "production", "g", "decimal", eggs_qs.exclude(average_egg_weight_g__isnull=True).aggregate(total=Avg("average_egg_weight_g"))["total"] or 0, {
        "period": period_avg_points(eggs_qs, "collection_date", "average_egg_weight_g"),
        "house": avg_points(eggs_qs, ["batch__house__house_code", "batch__house__name"], "average_egg_weight_g", house_label),
        "batch": avg_points(eggs_qs, "batch__batch_code", "average_egg_weight_g", batch_label),
    }, "bi-speedometer2")
    add_indicator("active_batches", "Active batches", "production", "batches", "number", active_batches.count(), {
        "house": active_batches_house,
        "period": current_snapshot_points(active_batches.count()),
    }, "bi-collection")
    add_indicator("live_birds", "Live birds", "production", "birds", "number", current_birds, {"period": current_snapshot_points(current_birds), "house": live_birds_house, "batch": live_birds_batch}, "bi-broadcast", featured=True)
    add_indicator("birds_stocked", "Birds stocked", "production", "birds", "number", sum(value for _, value in batch_initial), {
        "batch": top_points(batch_initial),
        "house": top_points(house_initial.items()),
        "period": current_snapshot_points(sum(value for _, value in batch_initial)),
    }, "bi-box-seam")
    add_indicator("house_capacity", "House capacity", "production", "birds", "number", total_capacity, {"house": capacity_house, "period": current_snapshot_points(total_capacity)}, "bi-grid-3x3-gap")
    add_indicator("house_utilization", "House utilization", "production", "%", "percent", pct(current_birds, total_capacity), {"house": top_points(house_utilization), "period": current_snapshot_points(pct(current_birds, total_capacity))}, "bi-house-check")
    add_indicator("batch_purchase_cost", "Batch purchase cost", "production", "UGX", "currency", sum(decimal_value(value) for _, value in batch_purchase_cost), {"batch": top_points(batch_purchase_cost), "period": current_snapshot_points(sum(decimal_value(value) for _, value in batch_purchase_cost))}, "bi-tags")

    add_indicator("feed_used_kg", "Feed used", "feed", "kg", "kg", total_feed, {"period": feed_period, "house": feed_house, "batch": feed_batch, "feed_type": sum_points(feed_qs, "feed_type", "quantity_kg", lambda row: feed_type_labels.get(row.get("feed_type"), row.get("feed_type") or "Other"))}, "bi-bag", featured=True)
    add_indicator("feed_per_egg", "Feed per egg", "feed", "kg", "ratio", (total_feed / decimal_value(total_eggs)) if total_eggs else 0, {
        "period": feed_per_egg_period,
        "house": ratio_points(feed_house, eggs_house, multiplier=1),
        "batch": ratio_points(feed_batch, eggs_batch, multiplier=1),
    }, "bi-sliders", featured=True)
    add_indicator("feed_mixture_kg", "Feed mixed", "feed", "kg", "kg", feed_mixtures_qs.aggregate(total=Sum("total_weight_kg"))["total"] or 0, {
        "period": period_sum_points(feed_mixtures_qs, "mix_date", "total_weight_kg"),
        "staff": sum_points(feed_mixtures_qs, ["mixed_by__first_name", "mixed_by__last_name", "mixed_by__username"], "total_weight_kg", lambda row: user_label(row, "mixed_by")),
    }, "bi-beaker")
    add_indicator("feed_records", "Feed records", "feed", "records", "number", feed_qs.count(), {
        "period": period_count_points(feed_qs, "record_date"),
        "house": count_points(feed_qs, ["batch__house__house_code", "batch__house__name"], house_label),
        "batch": count_points(feed_qs, "batch__batch_code", batch_label),
        "feed_type": count_points(feed_qs, "feed_type", lambda row: feed_type_labels.get(row.get("feed_type"), row.get("feed_type") or "Other")),
    }, "bi-list-check")
    add_indicator("feed_inventory_out", "Feed stock issued", "feed", "kg", "kg", feed_stock_out_qs.aggregate(total=Sum("quantity"))["total"] or 0, {
        "period": period_sum_points(feed_stock_out_qs, "tx_date", "quantity"),
        "inventory_item": sum_points(feed_stock_out_qs, "item__name", "quantity", lambda row: row.get("item__name") or "Unknown"),
        "store": sum_points(feed_stock_out_qs, "store__name", "quantity", lambda row: row.get("store__name") or "Unknown"),
    }, "bi-box-arrow-up")
    add_indicator("feed_purchase_cost", "Feed purchase cost", "feed", "UGX", "currency", feed_expense_qs.aggregate(total=Sum("total_amount"))["total"] or 0, {
        "period": period_sum_points(feed_expense_qs, "expense_date", "total_amount"),
        "expense_category": sum_points(feed_expense_qs, "category__name", "total_amount", lambda row: row.get("category__name") or "Feed"),
        "supplier": sum_points(feed_expense_qs, "supplier_name", "total_amount", lambda row: row.get("supplier_name") or "No supplier"),
    }, "bi-currency-exchange")

    add_indicator("deaths", "Deaths", "health", "birds", "number", total_deaths, {"period": deaths_period, "house": deaths_house, "batch": deaths_batch, "mortality_cause": sum_points(mortality_qs, "cause__name", "number_dead", lambda row: row.get("cause__name") or "Unknown")}, "bi-exclamation-triangle", featured=True)
    add_indicator("mortality_rate", "Mortality rate", "health", "%", "percent", pct(total_deaths, current_birds + total_deaths), {
        "period": mortality_rate_period,
        "house": ratio_points(deaths_house, combine_points(live_birds_house, deaths_house, lambda live, deaths: live + deaths)),
        "batch": ratio_points(deaths_batch, combine_points(live_birds_batch, deaths_batch, lambda live, deaths: live + deaths)),
    }, "bi-activity", featured=True)
    add_indicator("sickness_cases", "Sickness cases", "health", "cases", "number", sickness_qs.count(), {
        "period": period_count_points(sickness_qs, "date"),
        "house": count_points(sickness_qs, ["house_ref__house_code", "house_ref__name", "house"], lambda row: house_label(row, "house_ref")),
        "health_status": count_points(sickness_qs, "case_status", lambda row: sickness_status_labels.get(row.get("case_status"), row.get("case_status") or "Unknown")),
    }, "bi-clipboard-pulse", featured=True)
    add_indicator("birds_affected", "Birds affected", "health", "birds", "number", sickness_qs.aggregate(total=Sum("affected"))["total"] or 0, {
        "period": period_sum_points(sickness_qs, "date", "affected"),
        "house": sum_points(sickness_qs, ["house_ref__house_code", "house_ref__name", "house"], "affected", lambda row: house_label(row, "house_ref")),
        "health_status": sum_points(sickness_qs, "case_status", "affected", lambda row: sickness_status_labels.get(row.get("case_status"), row.get("case_status") or "Unknown")),
    }, "bi-heart-pulse")
    add_indicator("treatments_due", "Treatments due", "health", "items", "number", treatment_qs.filter(is_given=False).count(), {
        "period": period_count_points(treatment_qs.filter(is_given=False), "scheduled_for"),
        "staff": count_points(treatment_qs.filter(is_given=False), ["created_by__first_name", "created_by__last_name", "created_by__username"], lambda row: user_label(row, "created_by")),
    }, "bi-capsule")
    add_indicator("treatments_given", "Treatments given", "health", "items", "number", treatment_qs.filter(is_given=True).count(), {
        "period": period_count_points(treatment_qs.filter(is_given=True), "scheduled_for"),
        "staff": count_points(treatment_qs.filter(is_given=True), ["marked_given_by__first_name", "marked_given_by__last_name", "marked_given_by__username"], lambda row: user_label(row, "marked_given_by")),
    }, "bi-check2-square")
    add_indicator("vaccinations_scheduled", "Vaccinations scheduled", "health", "schedules", "number", vaccination_qs.count(), {
        "period": period_count_points(vaccination_qs, "scheduled_for"),
        "health_status": count_points(vaccination_qs, "status", lambda row: vaccination_status_labels.get(row.get("status"), row.get("status") or "Unknown")),
        "house": count_points(vaccination_qs, ["house_ref__house_code", "house_ref__name"], lambda row: house_label(row, "house_ref")),
    }, "bi-calendar2-heart")
    add_indicator("vaccinations_administered", "Vaccinations administered", "health", "birds", "number", vaccination_qs.filter(status=VaccinationSchedule.Status.ADMINISTERED).aggregate(total=Sum("number_vaccinated"))["total"] or 0, {
        "period": period_sum_points(vaccination_qs.filter(status=VaccinationSchedule.Status.ADMINISTERED), "scheduled_for", "number_vaccinated"),
        "house": sum_points(vaccination_qs.filter(status=VaccinationSchedule.Status.ADMINISTERED), ["house_ref__house_code", "house_ref__name"], "number_vaccinated", lambda row: house_label(row, "house_ref")),
    }, "bi-shield-check")
    add_indicator("vaccinations_overdue", "Vaccinations overdue", "health", "schedules", "number", vaccination_qs.filter(status=VaccinationSchedule.Status.SCHEDULED, scheduled_for__lt=now()).count(), {
        "period": period_count_points(vaccination_qs.filter(status=VaccinationSchedule.Status.SCHEDULED, scheduled_for__lt=now()), "scheduled_for"),
        "house": count_points(vaccination_qs.filter(status=VaccinationSchedule.Status.SCHEDULED, scheduled_for__lt=now()), ["house_ref__house_code", "house_ref__name"], lambda row: house_label(row, "house_ref")),
    }, "bi-alarm")
    add_indicator("health_events", "Health events", "health", "events", "number", health_event_qs.count(), {
        "period": period_count_points(health_event_qs, "event_date"),
        "house": count_points(health_event_qs, ["batch__house__house_code", "batch__house__name"], house_label),
        "health_status": count_points(health_event_qs, "event_type", lambda row: health_event_labels.get(row.get("event_type"), row.get("event_type") or "Other")),
    }, "bi-journal-medical")
    add_indicator("sickbay_cleanings", "Sickbay cleanings", "health", "records", "number", sickbay_cleaning_qs.count(), {"period": period_count_points(sickbay_cleaning_qs, "record_date")}, "bi-droplet")

    add_indicator("cleaning_records", "Cleaning records", "operations", "records", "number", cleaning_qs.count(), {
        "period": period_count_points(cleaning_qs, "record_date"),
        "house": count_points(cleaning_qs, ["batch__house__house_code", "batch__house__name"], house_label),
        "batch": count_points(cleaning_qs, "batch__batch_code", batch_label),
    }, "bi-brush")
    add_indicator("houses_cleaned", "House cleaned checks", "operations", "checks", "number", cleaning_qs.filter(house_cleaned=True).count(), {
        "period": period_count_points(cleaning_qs.filter(house_cleaned=True), "record_date"),
        "house": count_points(cleaning_qs.filter(house_cleaned=True), ["batch__house__house_code", "batch__house__name"], house_label),
    }, "bi-house-check")
    add_indicator("disinfections_done", "Disinfections done", "operations", "checks", "number", cleaning_qs.filter(disinfection_done=True).count(), {
        "period": period_count_points(cleaning_qs.filter(disinfection_done=True), "record_date"),
        "house": count_points(cleaning_qs.filter(disinfection_done=True), ["batch__house__house_code", "batch__house__name"], house_label),
    }, "bi-stars")
    add_indicator("water_changes", "Water changes", "operations", "checks", "number", cleaning_qs.filter(water_changed=True).count(), {
        "period": period_count_points(cleaning_qs.filter(water_changed=True), "record_date"),
        "house": count_points(cleaning_qs.filter(water_changed=True), ["batch__house__house_code", "batch__house__name"], house_label),
    }, "bi-droplet-half")
    add_indicator("inventory_stock_in", "Stock in", "operations", "units", "decimal", stock_in_qs.aggregate(total=Sum("quantity"))["total"] or 0, {
        "period": period_sum_points(stock_in_qs, "tx_date", "quantity"),
        "inventory_item": sum_points(stock_in_qs, "item__name", "quantity", lambda row: row.get("item__name") or "Unknown"),
        "store": sum_points(stock_in_qs, "store__name", "quantity", lambda row: row.get("store__name") or "Unknown"),
    }, "bi-box-arrow-in-down")
    add_indicator("inventory_stock_out", "Stock out", "operations", "units", "decimal", stock_out_qs.aggregate(total=Sum("quantity"))["total"] or 0, {
        "period": period_sum_points(stock_out_qs, "tx_date", "quantity"),
        "inventory_item": sum_points(stock_out_qs, "item__name", "quantity", lambda row: row.get("item__name") or "Unknown"),
        "store": sum_points(stock_out_qs, "store__name", "quantity", lambda row: row.get("store__name") or "Unknown"),
    }, "bi-box-arrow-up")
    add_indicator("inventory_adjustments", "Stock adjustments", "operations", "units", "decimal", stock_adjust_qs.aggregate(total=Sum("quantity"))["total"] or 0, {
        "period": period_sum_points(stock_adjust_qs, "tx_date", "quantity"),
        "inventory_item": sum_points(stock_adjust_qs, "item__name", "quantity", lambda row: row.get("item__name") or "Unknown"),
        "store": sum_points(stock_adjust_qs, "store__name", "quantity", lambda row: row.get("store__name") or "Unknown"),
    }, "bi-arrow-left-right")
    add_indicator("inventory_balance_qty", "Stock balance quantity", "operations", "units", "decimal", inventory_balance_total, {
        "inventory_item": inventory_balance_item_points,
        "store": inventory_balance_store_points,
        "period": current_snapshot_points(inventory_balance_total),
    }, "bi-boxes")
    add_indicator("inventory_value", "Inventory value estimate", "operations", "UGX", "currency", inventory_value_total, {
        "inventory_item": inventory_value_item_points,
        "store": inventory_value_store_points,
        "period": current_snapshot_points(inventory_value_total),
    }, "bi-archive")
    add_indicator("low_stock_rules", "Low stock alerts", "operations", "items", "number", len(low_stock_items), {
        "inventory_item": top_points(low_stock_items),
        "period": current_snapshot_points(len(low_stock_items)),
    }, "bi-exclamation-diamond")

    add_indicator("active_workers", "Active workers", "workforce", "workers", "number", Worker.objects.filter(status=Worker.Status.ACTIVE).count(), {
        "period": current_snapshot_points(Worker.objects.filter(status=Worker.Status.ACTIVE).count()),
        "staff": top_points((worker.full_name, 1) for worker in Worker.objects.filter(status=Worker.Status.ACTIVE)),
    }, "bi-people")
    add_indicator("attendance_present", "Attendance present", "workforce", "records", "number", attendance_qs.filter(status=Attendance.AttendanceStatus.PRESENT).count(), {
        "period": period_count_points(attendance_qs.filter(status=Attendance.AttendanceStatus.PRESENT), "work_date"),
        "staff": count_points(attendance_qs.filter(status=Attendance.AttendanceStatus.PRESENT), "worker__full_name", lambda row: row.get("worker__full_name") or "Unknown"),
        "attendance_status": count_points(attendance_qs, "status", lambda row: attendance_status_labels.get(row.get("status"), row.get("status") or "Unknown")),
    }, "bi-person-check")
    add_indicator("attendance_absent", "Attendance absent", "workforce", "records", "number", attendance_qs.filter(status=Attendance.AttendanceStatus.ABSENT).count(), {
        "period": period_count_points(attendance_qs.filter(status=Attendance.AttendanceStatus.ABSENT), "work_date"),
        "staff": count_points(attendance_qs.filter(status=Attendance.AttendanceStatus.ABSENT), "worker__full_name", lambda row: row.get("worker__full_name") or "Unknown"),
    }, "bi-person-x")
    add_indicator("hours_worked", "Hours worked", "workforce", "hours", "decimal", attendance_qs.aggregate(total=Sum("hours_worked"))["total"] or 0, {
        "period": period_sum_points(attendance_qs, "work_date", "hours_worked"),
        "staff": sum_points(attendance_qs, "worker__full_name", "hours_worked", lambda row: row.get("worker__full_name") or "Unknown"),
    }, "bi-clock")
    add_indicator("wages_paid", "Wages paid", "workforce", "UGX", "currency", wages_qs.aggregate(total=Sum("amount"))["total"] or 0, {
        "period": period_sum_points(wages_qs, "payment_date", "amount"),
        "staff": sum_points(wages_qs, "worker__full_name", "amount", lambda row: row.get("worker__full_name") or "Unknown"),
        "batch": sum_points(wages_qs, "batch__batch_code", "amount", batch_label),
        "house": sum_points(wages_qs, ["batch__house__house_code", "batch__house__name"], "amount", house_label),
    }, "bi-cash-coin")
    add_indicator("salary_paid", "Salaries paid", "workforce", "UGX", "currency", salary_qs.aggregate(total=Sum("amount"))["total"] or 0, {
        "period": salary_period_points(salary_qs),
        "staff": sum_points(salary_qs, ["employee__first_name", "employee__last_name", "employee__username"], "amount", lambda row: user_label(row, "employee")),
    }, "bi-person-vcard")

    key_theme_overrides = {
        "cash_received": "cash_flow",
        "collection_rate": "cash_flow",
        "receivables_outstanding": "cash_flow",
        "invoices_issued": "cash_flow",
        "average_invoice_value": "sales_pricing",
        "sales_revenue": "sales_pricing",
        "manure_sales": "sales_pricing",
        "manure_quantity_sold": "sales_pricing",
        "feed_cost": "production_cost",
        "labour_cost": "production_cost",
        "cost_per_egg": "production_cost",
        "feed_cost_per_egg": "production_cost",
        "feed_per_egg": "production_cost",
        "feed_used_kg": "production_cost",
        "revenue_per_egg": "sales_pricing",
        "rejected_egg_loss": "risk_loss",
        "mortality_loss_estimate": "risk_loss",
        "batch_revenue": "batch_performance",
        "batch_allocated_expenses": "batch_performance",
        "batch_profit": "batch_performance",
        "active_batches": "batch_performance",
        "live_birds": "batch_performance",
        "birds_stocked": "batch_performance",
        "house_capacity": "batch_performance",
        "house_utilization": "batch_performance",
        "batch_purchase_cost": "batch_performance",
    }
    legacy_theme_map = {
        "returns": "profitability",
        "sales": "sales_pricing",
        "feed": "production_cost",
        "production": "batch_performance",
        "health": "risk_loss",
        "operations": "risk_loss",
        "workforce": "production_cost",
    }
    for indicator in indicators:
        indicator["theme"] = key_theme_overrides.get(
            indicator["key"],
            legacy_theme_map.get(indicator["theme"], indicator["theme"]),
        )

    theme_meta = {
        "profitability": {"label": "Profitability", "icon": "bi-bank"},
        "cash_flow": {"label": "Cash Flow", "icon": "bi-wallet2"},
        "production_cost": {"label": "Cost of Production", "icon": "bi-coin"},
        "sales_pricing": {"label": "Sales & Pricing", "icon": "bi-cart-check"},
        "batch_performance": {"label": "Batch / House Performance", "icon": "bi-house-gear"},
        "risk_loss": {"label": "Risk & Loss Drivers", "icon": "bi-exclamation-triangle"},
    }
    themes = []
    for key, meta in theme_meta.items():
        themes.append({
            **meta,
            "key": key,
            "count": sum(1 for indicator in indicators if indicator["theme"] == key),
        })

    return {
        "range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "groupBy": group_by,
        },
        "themes": themes,
        "dimensions": [
            {"key": "period", "label": {"day": "Daily trend", "week": "Weekly trend", "month": "Monthly trend"}.get(group_by, "Trend"), "icon": "bi-calendar3"},
            {"key": "house", "label": "House", "icon": "bi-house"},
            {"key": "batch", "label": "Batch", "icon": "bi-collection"},
            {"key": "product", "label": "Product", "icon": "bi-basket"},
            {"key": "customer", "label": "Customer", "icon": "bi-person-lines-fill"},
            {"key": "expense_category", "label": "Expense category", "icon": "bi-receipt"},
            {"key": "supplier", "label": "Supplier", "icon": "bi-truck"},
            {"key": "payment_method", "label": "Payment method", "icon": "bi-credit-card"},
            {"key": "capital_type", "label": "Capital type", "icon": "bi-safe"},
            {"key": "feed_type", "label": "Feed type", "icon": "bi-bag"},
            {"key": "mortality_cause", "label": "Mortality cause", "icon": "bi-exclamation-triangle"},
            {"key": "health_status", "label": "Health status", "icon": "bi-clipboard-pulse"},
            {"key": "inventory_item", "label": "Inventory item", "icon": "bi-box"},
            {"key": "store", "label": "Store", "icon": "bi-shop"},
            {"key": "staff", "label": "Staff", "icon": "bi-people"},
            {"key": "attendance_status", "label": "Attendance status", "icon": "bi-person-check"},
        ],
        "presets": [
            {"key": "loss_drivers", "label": "Why am I making a loss?", "description": "Compare revenue, expenses, profit margin, and the expense ratio over time.", "theme": "profitability", "dimension": "period", "view": "bar", "indicators": ["sales_revenue", "total_expenses", "profit", "profit_margin", "expense_ratio"]},
            {"key": "high_costs", "label": "Which costs are too high?", "description": "Rank expense categories, feed cost, labour cost, and cost per saleable egg.", "theme": "production_cost", "dimension": "expense_category", "view": "bar", "indicators": ["total_expenses", "feed_cost", "labour_cost", "cost_per_egg", "feed_cost_per_egg"]},
            {"key": "batch_profitability", "label": "Which batch or house is most profitable?", "description": "Compare batch revenue, allocated expenses, and profit by batch or house.", "theme": "batch_performance", "dimension": "batch", "view": "bar", "indicators": ["batch_revenue", "batch_allocated_expenses", "batch_profit", "eggs_collected", "feed_used_kg"]},
            {"key": "feed_margin", "label": "Is feed cost hurting profit?", "description": "Connect feed spend, feed usage, egg output, and feed cost per egg.", "theme": "production_cost", "dimension": "period", "view": "line", "indicators": ["feed_cost", "feed_used_kg", "feed_per_egg", "feed_cost_per_egg", "profit"]},
            {"key": "customer_cash", "label": "Which customers owe the farm?", "description": "Find customers driving receivables, sales, and cash collections.", "theme": "cash_flow", "dimension": "customer", "view": "bar", "indicators": ["sales_revenue", "cash_received", "receivables_outstanding", "collection_rate", "invoices_issued"]},
            {"key": "expand_safely", "label": "Can I expand safely?", "description": "Check profit, cash collection, cost per egg, mortality, and house utilization.", "theme": "profitability", "dimension": "period", "view": "bar", "indicators": ["profit", "profit_margin", "collection_rate", "cost_per_egg", "mortality_rate", "house_utilization"]},
        ],
        "defaultSelection": ["sales_revenue", "total_expenses", "profit", "profit_margin", "cost_per_egg", "cash_received"],
        "indicators": indicators,
    }


def _build_investor_financial_context(start_date, end_date, group_by):
    def number(value):
        return float(value or 0)

    def decimal_value(value):
        return Decimal(str(value or 0))

    def decimal_total(queryset, field_name):
        return queryset.aggregate(
            total=Coalesce(
                Sum(field_name),
                Value(0),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )["total"]

    def pct(numerator, denominator):
        denominator = decimal_value(denominator)
        if denominator == 0:
            return Decimal("0")
        return decimal_value(numerator) / denominator * Decimal("100")

    def grouped_series(queryset, date_field, value_field):
        grouped = {}
        current = start_date
        while current <= end_date:
            if group_by == "week":
                key_date = current - timedelta(days=current.weekday())
                label = f"Week of {key_date.strftime('%d %b %Y')}"
            elif group_by == "day":
                label = current.strftime("%d %b %Y")
            else:
                label = current.strftime("%b %Y")
            grouped.setdefault(label, Decimal("0"))
            current += timedelta(days=1)

        for row in queryset.values(date_field).annotate(total=Sum(value_field)).order_by(date_field):
            row_date = row.get(date_field)
            if not row_date:
                continue
            if group_by == "week":
                key_date = row_date - timedelta(days=row_date.weekday())
                label = f"Week of {key_date.strftime('%d %b %Y')}"
            elif group_by == "day":
                label = row_date.strftime("%d %b %Y")
            else:
                label = row_date.strftime("%b %Y")
            grouped[label] = grouped.get(label, Decimal("0")) + decimal_value(row["total"])

        return {
            "labels": list(grouped.keys()),
            "values": [number(value) for value in grouped.values()],
            "map": grouped,
        }

    sales_qs = SaleInvoice.objects.exclude(status=SaleInvoice.Status.CANCELLED).filter(invoice_date__range=(start_date, end_date))
    sale_items_qs = SaleItem.objects.select_related("invoice", "invoice__customer", "batch__house").filter(invoice__in=sales_qs)
    payments_qs = CustomerPayment.objects.select_related("customer", "invoice").filter(payment_date__range=(start_date, end_date))
    expenses_qs = ExpenseTransaction.objects.exclude(status=ExpenseTransaction.Status.REJECTED).select_related("category").filter(expense_date__range=(start_date, end_date))
    eggs_qs = egg_collection.objects.select_related("batch__house").filter(collection_date__range=(start_date, end_date), status=ApprovalStatus.APPROVED)
    mortality_qs = MortalityRecord.objects.select_related("batch__house", "cause").filter(record_date__range=(start_date, end_date), status=ApprovalStatus.APPROVED)
    feed_qs = FeedRecord.objects.select_related("batch__house").filter(record_date__range=(start_date, end_date), status=ApprovalStatus.APPROVED)
    wages_qs = WagePayment.objects.select_related("worker", "batch__house").filter(payment_date__range=(start_date, end_date))
    salary_qs = SalaryPayment.objects.select_related("employee").filter(period_month__gte=start_date.strftime("%Y-%m"), period_month__lte=end_date.strftime("%Y-%m"))

    feed_expense_qs = expenses_qs.filter(
        Q(category__code__icontains="FEED") | Q(category__name__icontains="feed") | Q(item_name__icontains="feed")
    )
    labour_expense_qs = expenses_qs.filter(
        Q(category__code__icontains="LAB") | Q(category__name__icontains="labour") | Q(category__name__icontains="labor")
    )
    egg_sale_items_qs = sale_items_qs.filter(product_name__icontains="egg")
    expense_allocations_qs = ExpenseAllocation.objects.select_related("batch__house", "expense").filter(expense__in=expenses_qs)

    total_revenue = decimal_total(sales_qs, "total_amount")
    total_cash_received = decimal_total(payments_qs, "amount")
    total_expenses = decimal_total(expenses_qs, "total_amount")
    total_profit = total_revenue - total_expenses
    outstanding_balance = ReceivableLedger.objects.aggregate(
        total=Coalesce(
            Sum("balance"),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    )["total"]
    total_eggs = eggs_qs.aggregate(total=Sum("eggs_collected"))["total"] or 0
    total_rejected_eggs = eggs_qs.aggregate(total=Sum("eggs_rejected"))["total"] or 0
    saleable_eggs = max(int(total_eggs or 0) - int(total_rejected_eggs or 0), 0)
    total_deaths = mortality_qs.aggregate(total=Sum("number_dead"))["total"] or 0
    total_feed_kg = feed_qs.aggregate(total=Sum("quantity_kg"))["total"] or Decimal("0")
    feed_cost = decimal_total(feed_expense_qs, "total_amount")
    labour_cost = (
        decimal_total(wages_qs, "amount")
        + decimal_total(salary_qs, "amount")
        + decimal_total(labour_expense_qs, "total_amount")
    )
    egg_sales_revenue = decimal_total(egg_sale_items_qs, "line_total")

    active_batches = PoultryBatch.objects.filter(status=PoultryBatch.Status.ACTIVE).select_related("house")
    current_birds = 0
    stocked_birds = 0
    batch_purchase_total = Decimal("0")
    for batch in active_batches:
        approved_deaths = batch.mortality_records.filter(status=ApprovalStatus.APPROVED).aggregate(total=Sum("number_dead"))["total"] or 0
        birds_sold = SaleItem.objects.filter(batch=batch).filter(
            Q(product_name__icontains="bird") | Q(product_name__icontains="off layer")
        ).aggregate(total=Sum("quantity"))["total"] or 0
        current_birds += max(batch.initial_quantity - int(approved_deaths) - int(birds_sold), 0)
        stocked_birds += batch.initial_quantity
        batch_purchase_total += decimal_value(batch.amount_paid)

    expense_ratio = pct(total_expenses, total_revenue)
    profit_margin = pct(total_profit, total_revenue)
    collection_rate = pct(total_cash_received, total_revenue)
    rejected_egg_rate = pct(total_rejected_eggs, total_eggs)
    egg_yield = (Decimal(total_eggs) / Decimal(current_birds)) if current_birds else Decimal("0")
    feed_per_egg = (total_feed_kg / Decimal(saleable_eggs)) if saleable_eggs else Decimal("0")
    cost_per_egg = (total_expenses / Decimal(saleable_eggs)) if saleable_eggs else Decimal("0")
    feed_cost_per_egg = (feed_cost / Decimal(saleable_eggs)) if saleable_eggs else Decimal("0")
    revenue_per_egg = (egg_sales_revenue / Decimal(saleable_eggs)) if saleable_eggs else Decimal("0")
    mortality_rate = pct(total_deaths, current_birds + total_deaths)
    average_bird_cost = (batch_purchase_total / Decimal(stocked_birds)) if stocked_birds else Decimal("0")
    rejected_egg_loss = Decimal(total_rejected_eggs or 0) * revenue_per_egg
    mortality_loss_estimate = Decimal(total_deaths or 0) * average_bird_cost

    expense_breakdown = []
    for row in expenses_qs.values("category__name").annotate(total=Sum("total_amount")).order_by("-total")[:8]:
        amount = decimal_value(row["total"])
        expense_breakdown.append({
            "label": row["category__name"] or "Uncategorised",
            "value": number(amount),
            "share": number(pct(amount, total_expenses)),
        })

    sales_breakdown = [
        {"label": row["product_name"] or "Sales", "value": number(row["total"])}
        for row in sale_items_qs.values("product_name").annotate(total=Sum("line_total")).order_by("-total")[:8]
    ]
    payment_breakdown = [
        {"label": row["method"] or "Unknown", "value": number(row["total"])}
        for row in payments_qs.values("method").annotate(total=Sum("amount")).order_by("-total")
    ]

    revenue_series = grouped_series(sales_qs, "invoice_date", "total_amount")
    expense_series = grouped_series(expenses_qs, "expense_date", "total_amount")
    cash_series = grouped_series(payments_qs, "payment_date", "amount")
    egg_series = grouped_series(eggs_qs, "collection_date", "eggs_collected")
    rejected_series = grouped_series(eggs_qs, "collection_date", "eggs_rejected")
    feed_series = grouped_series(feed_qs, "record_date", "quantity_kg")
    mortality_series = grouped_series(mortality_qs, "record_date", "number_dead")
    profit_values = [
        number(revenue_series["map"].get(label, Decimal("0")) - expense_series["map"].get(label, Decimal("0")))
        for label in revenue_series["labels"]
    ]

    batch_revenue = {
        row["batch__batch_code"] or "Unassigned": decimal_value(row["total"])
        for row in sale_items_qs.filter(batch__isnull=False).values("batch__batch_code").annotate(total=Sum("line_total"))
    }
    batch_expenses = {
        row["batch__batch_code"] or "Unassigned": decimal_value(row["total"])
        for row in expense_allocations_qs.filter(batch__isnull=False).values("batch__batch_code").annotate(total=Sum("amount_allocated"))
    }
    batch_profitability = []
    for label in sorted(set(batch_revenue) | set(batch_expenses)):
        revenue = batch_revenue.get(label, Decimal("0"))
        expenses = batch_expenses.get(label, Decimal("0"))
        profit = revenue - expenses
        batch_profitability.append({
            "label": label,
            "revenue": revenue,
            "expenses": expenses,
            "profit": profit,
            "margin": pct(profit, revenue),
        })
    batch_profitability.sort(key=lambda item: item["profit"])

    if total_profit < 0:
        financial_health = {
            "level": "danger",
            "label": "Loss-Making",
            "title": "The farm is currently losing money",
            "summary": f"Expenses exceed revenue by UGX {abs(total_profit):,.0f} in this period.",
            "action": "Freeze expansion decisions until feed, labour, veterinary, and pricing drivers are reviewed.",
        }
    elif total_revenue > 0 and outstanding_balance > total_revenue * Decimal("0.25"):
        financial_health = {
            "level": "warning",
            "label": "Cash Risk",
            "title": "Profit depends on tighter cash collection",
            "summary": f"Receivables are UGX {outstanding_balance:,.0f}, above 25% of period revenue.",
            "action": "Prioritize collections and tighten credit terms before increasing operating spend.",
        }
    elif expense_ratio > Decimal("70"):
        financial_health = {
            "level": "warning",
            "label": "High Cost",
            "title": "Costs are consuming most revenue",
            "summary": f"Expenses are {expense_ratio:.1f}% of revenue.",
            "action": "Review the largest cost categories and set weekly cost-per-egg targets.",
        }
    elif current_birds and egg_yield < Decimal("0.55"):
        financial_health = {
            "level": "warning",
            "label": "Low Yield",
            "title": "Production is weakening the financial result",
            "summary": f"Egg yield is {egg_yield:.2f} eggs per live bird.",
            "action": "Check feed ration, bird age, house conditions, disease pressure, and rejected eggs.",
        }
    else:
        financial_health = {
            "level": "success",
            "label": "Profitable",
            "title": "The farm is financially healthy for this period",
            "summary": f"Net profit is UGX {total_profit:,.0f} with a {profit_margin:.1f}% margin.",
            "action": "Keep watching cost per egg, receivables, and batch-level profitability before expanding.",
        }

    insights = []

    def add_insight(level, title, evidence, impact, recommendation):
        insights.append({
            "level": level,
            "title": title,
            "evidence": evidence,
            "impact": impact,
            "recommendation": recommendation,
            "text": f"{evidence} {recommendation}",
        })

    if total_profit < 0:
        add_insight(
            "danger",
            "Loss risk",
            f"Net profit is UGX {total_profit:,.0f}.",
            "The farm is using more cash than it generates from sales.",
            "Review feed, labour, veterinary costs, and selling prices before adding more birds.",
        )
    elif profit_margin < Decimal("15") and total_revenue > 0:
        add_insight(
            "warning",
            "Thin margin",
            f"Profit margin is {profit_margin:.1f}%.",
            "A small change in feed cost, mortality, or price could push the farm into loss.",
            "Raise pricing discipline and reduce the largest cost categories first.",
        )
    else:
        add_insight(
            "success",
            "Margin is healthy",
            f"Profit margin is {profit_margin:.1f}%.",
            "The farm can defend operating cash if collections remain strong.",
            "Keep monitoring cost per egg and batch-level profit before expanding.",
        )

    if outstanding_balance > total_revenue * Decimal("0.25") and total_revenue > 0:
        add_insight(
            "warning",
            "Receivables need attention",
            f"Outstanding receivables are UGX {outstanding_balance:,.0f}.",
            "Sales may look strong while cash remains trapped with customers.",
            "Prioritize overdue customers and tighten credit limits for slow payers.",
        )
    if expense_ratio > Decimal("70"):
        add_insight(
            "warning",
            "High cost base",
            f"Expenses are {expense_ratio:.1f}% of revenue.",
            "Margins will stay fragile until the largest cost drivers are reduced.",
            "Set targets for feed cost, labour cost, and cost per saleable egg.",
        )
    if feed_cost and total_expenses and feed_cost > total_expenses * Decimal("0.35"):
        add_insight(
            "warning",
            "Feed cost pressure",
            f"Feed cost is UGX {feed_cost:,.0f}, {pct(feed_cost, total_expenses):.1f}% of expenses.",
            "Feed is likely the biggest lever in cost of production.",
            "Compare feed allocation with egg output by house and batch.",
        )
    if cost_per_egg and revenue_per_egg and cost_per_egg > revenue_per_egg:
        add_insight(
            "danger",
            "Egg unit economics are negative",
            f"Cost per saleable egg is UGX {cost_per_egg:,.0f} against estimated revenue per egg of UGX {revenue_per_egg:,.0f}.",
            "Each egg may be sold below its production cost.",
            "Review tray price, rejected eggs, feed ration, and cost allocation immediately.",
        )
    if mortality_rate > Decimal("3"):
        add_insight(
            "danger",
            "Mortality above target",
            f"Mortality rate is {mortality_rate:.2f}%.",
            "Bird losses reduce production capacity and raise unit cost.",
            "Check health reports, house conditions, vaccination follow-up, and feed quality.",
        )
    if current_birds and egg_yield < Decimal("0.55"):
        add_insight(
            "warning",
            "Egg yield is low",
            f"Egg yield is {egg_yield:.2f} eggs per live bird.",
            "Lower output increases cost per egg and weakens profitability.",
            "Review bird age, lighting, feed ration, disease pressure, and rejection causes.",
        )

    chart_data = {
        "profit": {
            "title": "Profit and loss",
            "unit": "UGX",
            "series": [
                {"label": "Revenue", "labels": revenue_series["labels"], "values": revenue_series["values"]},
                {"label": "Expenses", "labels": expense_series["labels"], "values": expense_series["values"]},
                {"label": "Profit", "labels": revenue_series["labels"], "values": profit_values},
            ],
        },
        "sales": {
            "title": "Sales revenue",
            "unit": "UGX",
            "series": [
                {"label": "Sales", "labels": revenue_series["labels"], "values": revenue_series["values"]},
                {"label": "Cash received", "labels": cash_series["labels"], "values": cash_series["values"]},
            ],
            "pie": sales_breakdown,
        },
        "expenses": {
            "title": "Expense movement",
            "unit": "UGX",
            "series": [{"label": "Expenses", "labels": expense_series["labels"], "values": expense_series["values"]}],
            "pie": expense_breakdown,
        },
        "eggs": {
            "title": "Egg production",
            "unit": "eggs",
            "series": [
                {"label": "Collected eggs", "labels": egg_series["labels"], "values": egg_series["values"]},
                {"label": "Rejected eggs", "labels": rejected_series["labels"], "values": rejected_series["values"]},
            ],
        },
        "health": {
            "title": "Mortality and feed",
            "unit": "count / kg",
            "series": [
                {"label": "Deaths", "labels": mortality_series["labels"], "values": mortality_series["values"]},
                {"label": "Feed used kg", "labels": feed_series["labels"], "values": feed_series["values"]},
            ],
        },
        "cash": {
            "title": "Cash collection",
            "unit": "UGX",
            "series": [{"label": "Cash received", "labels": cash_series["labels"], "values": cash_series["values"]}],
            "pie": payment_breakdown,
        },
    }

    financial_chart_data = {
        "profitTrend": chart_data["profit"],
        "costDrivers": expense_breakdown,
        "unitEconomics": [
            {"label": "Cost / saleable egg", "value": number(cost_per_egg)},
            {"label": "Feed cost / egg", "value": number(feed_cost_per_egg)},
            {"label": "Revenue / egg", "value": number(revenue_per_egg)},
            {"label": "Rejected egg loss", "value": number(rejected_egg_loss)},
        ],
        "cashRisk": [
            {"label": "Cash collected", "value": number(total_cash_received)},
            {"label": "Receivables", "value": number(outstanding_balance)},
            {"label": "Uncollected period sales", "value": number(max(total_revenue - total_cash_received, Decimal("0")))},
        ],
    }

    recent_invoices = sales_qs.select_related("customer", "created_by").order_by("-created_at")[:5]
    recent_expenses = expenses_qs.select_related("category", "created_by").order_by("-expense_date", "-created_at")[:5]
    recent_eggs = eggs_qs.select_related("batch__house", "collected_by").order_by("-collection_date", "-collected_at")[:5]

    return {
        "metrics": {
            "total_revenue": total_revenue,
            "gross_revenue": total_revenue,
            "cash_received": total_cash_received,
            "cash_collected": total_cash_received,
            "total_expenses": total_expenses,
            "profit": total_profit,
            "net_profit": total_profit,
            "profit_margin": profit_margin,
            "expense_ratio": expense_ratio,
            "collection_rate": collection_rate,
            "outstanding_balance": outstanding_balance,
            "unpaid_receivables": outstanding_balance,
            "total_eggs": total_eggs,
            "eggs_produced": total_eggs,
            "saleable_eggs": saleable_eggs,
            "rejected_eggs": total_rejected_eggs,
            "rejected_egg_rate": rejected_egg_rate,
            "total_deaths": total_deaths,
            "mortality_rate": mortality_rate,
            "feed_used_kg": total_feed_kg,
            "feed_per_egg": feed_per_egg,
            "feed_cost": feed_cost,
            "feed_cost_per_egg": feed_cost_per_egg,
            "labour_cost": labour_cost,
            "cost_per_egg": cost_per_egg,
            "revenue_per_egg": revenue_per_egg,
            "rejected_egg_loss": rejected_egg_loss,
            "mortality_loss_estimate": mortality_loss_estimate,
            "current_birds": current_birds,
            "active_batches": active_batches.count(),
            "egg_yield": egg_yield,
        },
        "financial_health": financial_health,
        "chart_data": chart_data,
        "financial_chart_data": financial_chart_data,
        "expense_breakdown": expense_breakdown,
        "sales_breakdown": sales_breakdown,
        "payment_breakdown": payment_breakdown,
        "batch_profitability": batch_profitability[:8],
        "insights": insights[:6],
        "recent_invoices": recent_invoices,
        "recent_expenses": recent_expenses,
        "recent_eggs": recent_eggs,
    }


@worker_required
def record_feed(request):
    batches = _get_worker_active_batches(request.user)
    available_mixtures = _mixtures_for_worker_batches(batches)

    if request.method == "POST":
        batch_id = request.POST.get("batch", "").strip()
        feed_mixture_id = request.POST.get("feed_mixture", "").strip()
        quantity_raw = request.POST.get("quantity", "").strip()
        notes = request.POST.get("notes", "").strip()
        photos = request.FILES.getlist('photos')

        errors = []
        current_time = localtime(now())
        record_date = current_time.date()
        parsed_time = current_time.time().replace(microsecond=0)
        selected_batch = _selected_or_only(batches, batch_id)
        selected_mixture = None

        if not selected_batch:
            errors.append("Please select a valid batch from your assigned houses.")
        elif feed_mixture_id:
            selected_mixture = available_mixtures.filter(pk=feed_mixture_id).filter(
                Q(allocations__batch=selected_batch)
                | Q(allocations__batch__isnull=True, allocations__house=selected_batch.house)
            ).first()
            if not selected_mixture:
                errors.append("Please select a mixture assigned to this flock.")
        else:
            errors.append("Please select a supervisor feed mixture.")

        try:
            quantity_kg = Decimal(quantity_raw)
            if quantity_kg <= 0:
                errors.append("Quantity must be greater than zero.")
        except (InvalidOperation, ValueError):
            quantity_kg = None
            errors.append("Please provide a valid quantity.")

        if selected_mixture and selected_batch and quantity_kg is not None:
            allocation = selected_mixture.allocations.filter(batch=selected_batch).first()
            if allocation is None:
                allocation = selected_mixture.allocations.filter(
                    batch__isnull=True,
                    house=selected_batch.house,
                ).first()
            allocated_kg = allocation.quantity_kg if allocation else Decimal("0.00")
            recorded_feed = FeedRecord.objects.filter(
                feed_mixture=selected_mixture,
                record_date=record_date or date.today(),
            ).exclude(status=ApprovalStatus.REJECTED)
            if allocation and allocation.batch_id:
                recorded_feed = recorded_feed.filter(batch=selected_batch)
            else:
                recorded_feed = recorded_feed.filter(batch__house=selected_batch.house)
            already_recorded_kg = recorded_feed.aggregate(total=Sum("quantity_kg"))["total"] or Decimal("0.00")
            if already_recorded_kg + quantity_kg > allocated_kg:
                remaining_kg = max(allocated_kg - already_recorded_kg, Decimal("0.00"))
                errors.append(
                    f"This house has {remaining_kg} kg remaining from {selected_mixture.name}."
                )

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            FeedRecord.objects.create(
                batch=selected_batch,
                feed_mixture=selected_mixture,
                record_date=record_date,
                feed_type=FeedRecord.FeedType.OTHER,
                quantity_kg=quantity_kg,
                time_given=parsed_time,
                notes=notes,
                recorded_by=request.user,
            )
            messages.success(request, "Feed record saved successfully.")
            return redirect("record_feed")

    recent_feed_records = FeedRecord.objects.filter(
        recorded_by=request.user,
        batch__in=batches,
        record_date=localdate(),
    ).select_related("batch__house", "feed_mixture")[:10]

    return render(
        request,
        "record_feed.html",
        {
            "batches": batches,
            "today": localdate(),
            "recent_feed_records": recent_feed_records,
            "feed_mixtures": available_mixtures,
        },
    )

@worker_required
def record_egg(request):
    batches = _get_worker_active_batches(request.user)

    if request.method == "POST":
        batch_id = request.POST.get("batch", "").strip()
        collection_date = localdate()
        total_eggs_raw = request.POST.get("total_eggs", "").strip()
        broken_eggs_raw = request.POST.get("broken_eggs", "0").strip()
        egg_weight_values = request.POST.getlist("egg_weights")
        notes = request.POST.get("notes", "").strip()

        errors = []
        selected_batch = _selected_or_only(batches, batch_id)
        average_egg_weight_g = None

        if not selected_batch:
            errors.append("Please select a valid batch from your assigned houses.")

        try:
            eggs_collected = int(total_eggs_raw)
            if eggs_collected < 0:
                errors.append("Total eggs cannot be negative.")
        except ValueError:
            eggs_collected = None
            errors.append("Please provide a valid total eggs value.")

        try:
            eggs_rejected = int(broken_eggs_raw or "0")
            if eggs_rejected < 0:
                errors.append("Broken eggs cannot be negative.")
        except ValueError:
            eggs_rejected = None
            errors.append("Please provide a valid broken eggs value.")

        if (
            eggs_collected is not None
            and eggs_rejected is not None
            and eggs_rejected > eggs_collected
        ):
            errors.append("Broken eggs cannot be more than total eggs.")

        weights = []
        for raw_weight in egg_weight_values:
            raw_weight = raw_weight.strip()
            if not raw_weight:
                continue
            try:
                weight = Decimal(raw_weight)
            except InvalidOperation:
                errors.append("Please enter valid egg weights.")
                weights = []
                break
            if weight <= 0:
                errors.append("Egg weights must be greater than zero.")
                weights = []
                break
            weights.append(weight)

        if eggs_collected is not None and eggs_collected > 0:
            if eggs_collected < 50:
                if len(weights) != eggs_collected:
                    errors.append(
                        f"Because fewer than 50 eggs were collected, enter the weight for all {eggs_collected} eggs."
                    )
            elif len(weights) < 20 or len(weights) > 30:
                errors.append("Enter 20 to 30 random egg weights for collections of 50 eggs or more.")

            if weights and not errors:
                average_egg_weight_g = (sum(weights) / Decimal(len(weights))).quantize(Decimal("0.01"))

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            egg_collection.objects.create(
                batch=selected_batch,
                collection_date=collection_date,
                eggs_collected=eggs_collected,
                eggs_rejected=eggs_rejected,
                average_egg_weight_g=average_egg_weight_g,
                notes=notes,
                collected_by=request.user,
            )
            messages.success(request, "Egg collection record saved successfully.")
            return redirect("record_egg")

    recent_egg_records = egg_collection.objects.filter(
        collected_by=request.user,
        batch__in=batches,
        collection_date=localdate(),
    ).select_related("batch__house")[:10]

    return render(
        request,
        "record_egg.html",
        {
            "batches": batches,
            "today": localdate(),
            "recent_egg_records": recent_egg_records,
        },
    )


@supervisor_required
def feed_mixtures(request):
    role_code = _role_code(request.user)
    batches = PoultryBatch.objects.filter(
        status=PoultryBatch.Status.ACTIVE,
        house__is_active=True,
    ).select_related("house").order_by("house__house_code", "batch_code")
    if role_code == "SUPERVISOR":
        batches = batches.filter(house__in=request.user.houses.all())
    batches = list(batches)
    batch_by_id = {str(batch.pk): batch for batch in batches}
    for batch in batches:
        batch.formula_stage = _batch_formula_stage(batch)
        batch.formula_stage_label = _stage_label(batch.formula_stage)
        batch.age_weeks = batch.current_age_days // 7

    store, _ = Store.objects.get_or_create(
        name="Main Store",
        defaults={"location_note": "Primary farm store", "is_active": True},
    )
    feed_items = list(
        Item.objects.filter(is_active=True, category__code__iexact="FEED")
        .select_related("category")
        .order_by("name")
    )
    feed_item_stock = {item.pk: _stock_for_item(item) for item in feed_items}
    for item in feed_items:
        item.available_kg = feed_item_stock.get(item.pk, Decimal("0.000"))

    formula_templates = list(
        FeedFormulaTemplate.objects.filter(is_active=True)
        .select_related("created_by")
        .prefetch_related("ingredients__item__category")
        .order_by("-is_system", "concentration_percent", "name", "flock_stage")
    )
    formula_by_id = {str(formula.pk): formula for formula in formula_templates}

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        mix_date_raw = request.POST.get("mix_date", "").strip()
        primary_batch_id = request.POST.get("primary_batch", "").strip()
        formula_id = request.POST.get("formula_template", "").strip()
        target_weight_raw = request.POST.get("target_weight_kg", "").strip()
        total_weight_raw = request.POST.get("total_weight_kg", "").strip()
        notes = request.POST.get("notes", "").strip()
        save_custom = request.POST.get("save_custom") == "on"
        custom_formula_name = request.POST.get("custom_formula_name", "").strip()
        ingredient_items = request.POST.getlist("ingredient_item")
        ingredient_quantities = request.POST.getlist("ingredient_quantity")
        allocation_batches = request.POST.getlist("allocation_batch")
        allocation_quantities = request.POST.getlist("allocation_quantity")

        errors = []
        ingredients = []
        allocations = []
        ingredient_totals_by_item = {}
        selected_formula = None
        primary_batch = batch_by_id.get(primary_batch_id)
        flock_stage = _batch_formula_stage(primary_batch) if primary_batch else ""
        target_weight_kg = None
        total_weight_kg = None
        saved_formula_name = ""

        if not primary_batch:
            errors.append("Please select a valid active flock from your assigned houses.")

        try:
            mix_date = date.fromisoformat(mix_date_raw)
        except ValueError:
            mix_date = None
            errors.append("Please provide a valid mixture date.")

        if mix_date and mix_date != date.today():
            errors.append("Feed mixtures can only be recorded for today.")

        try:
            target_weight_kg = Decimal(target_weight_raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if target_weight_kg <= 0:
                errors.append("Planned mixture kg must be greater than zero.")
        except (InvalidOperation, ValueError):
            errors.append("Please enter a valid planned mixture weight.")

        try:
            total_weight_kg = Decimal(total_weight_raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if total_weight_kg <= 0:
                errors.append("Total weighed kg must be greater than zero.")
        except (InvalidOperation, ValueError):
            errors.append("Please enter the total weighed kg after mixing.")

        if formula_id and formula_id != "custom":
            selected_formula = formula_by_id.get(formula_id)
            if not selected_formula:
                errors.append("Please select an active feed formula.")
            elif primary_batch and selected_formula.flock_stage != flock_stage:
                errors.append("The selected formula does not match this flock's current age stage.")
            elif target_weight_kg is not None:
                ingredients = scale_formula_ingredients(selected_formula, target_weight_kg)
                if not ingredients:
                    errors.append("The selected formula has no usable ingredients.")
                for ingredient in ingredients:
                    item = ingredient["item"]
                    if not item.is_active or item.category.code.upper() != "FEED":
                        errors.append(f"{item.name} is not an active feed inventory item.")
        elif formula_id == "custom":
            seen_item_ids = set()
            for item_id, quantity_raw in zip(ingredient_items, ingredient_quantities):
                quantity_raw = quantity_raw.strip()
                if not item_id and not quantity_raw:
                    continue
                item = next((candidate for candidate in feed_items if str(candidate.pk) == item_id), None)
                if not item:
                    errors.append("Please select a valid active feed stock item.")
                    continue
                if item.pk in seen_item_ids:
                    errors.append(f"{item.name} can only appear once in a custom formula.")
                    continue
                seen_item_ids.add(item.pk)
                try:
                    quantity_kg = Decimal(quantity_raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    if quantity_kg <= 0:
                        errors.append("Ingredient kg must be greater than zero.")
                        continue
                except (InvalidOperation, ValueError):
                    errors.append("Please enter valid ingredient kg values.")
                    continue
                ingredients.append({
                    "item": item,
                    "feed_type": FeedRecord.FeedType.OTHER,
                    "ingredient_name": item.name,
                    "quantity_kg": quantity_kg,
                })
            if save_custom:
                if not custom_formula_name:
                    errors.append("Name the custom formula before saving it for reuse.")
                elif primary_batch and FeedFormulaTemplate.objects.filter(
                    name__iexact=custom_formula_name,
                    flock_stage=flock_stage,
                    is_active=True,
                ).exists():
                    errors.append("An active formula with this name already exists for the flock stage.")
                else:
                    saved_formula_name = custom_formula_name
        else:
            errors.append("Please choose a standard, saved, or custom formula.")

        for batch_id, quantity_raw in zip(allocation_batches, allocation_quantities):
            quantity_raw = quantity_raw.strip()
            if not batch_id and not quantity_raw:
                continue
            batch = batch_by_id.get(batch_id)
            if not batch:
                errors.append("Please select valid active flocks for distribution.")
                continue
            if primary_batch and _batch_formula_stage(batch) != flock_stage:
                errors.append(f"{batch.batch_code} is not in the same feed stage as the primary flock.")
                continue
            try:
                quantity_kg = Decimal(quantity_raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if quantity_kg <= 0:
                    errors.append("Flock allocation kg must be greater than zero.")
                    continue
            except (InvalidOperation, ValueError):
                errors.append("Please enter valid flock allocation kg values.")
                continue
            allocations.append({"batch": batch, "house": batch.house, "quantity_kg": quantity_kg})

        allocation_batch_ids = [item["batch"].pk for item in allocations]
        if len(allocation_batch_ids) != len(set(allocation_batch_ids)):
            errors.append("Each flock should appear only once in the distribution list.")
        if primary_batch and primary_batch.pk not in allocation_batch_ids:
            errors.append("The primary flock must be included in the distribution.")

        if not ingredients:
            errors.append("Add at least one feed ingredient.")
        if not allocations:
            errors.append("Assign the mixture to at least one flock.")

        for ingredient in ingredients:
            item = ingredient["item"]
            ingredient_totals_by_item[item.pk] = (
                ingredient_totals_by_item.get(item.pk, Decimal("0.00"))
                + ingredient["quantity_kg"]
            )
        for item_id, requested_kg in ingredient_totals_by_item.items():
            item = next((candidate for candidate in feed_items if candidate.pk == item_id), None)
            if item is None and selected_formula:
                item = next(
                    (line["item"] for line in ingredients if line["item"].pk == item_id),
                    None,
                )
            available_kg = feed_item_stock.get(item_id, Decimal("0.000"))
            if item and requested_kg > available_kg:
                errors.append(f"Only {available_kg} kg of {item.name} is available; {requested_kg} kg is required.")

        total_ingredients = sum((item["quantity_kg"] for item in ingredients), Decimal("0.00"))
        total_allocations = sum((item["quantity_kg"] for item in allocations), Decimal("0.00"))
        if target_weight_kg is not None and total_ingredients != target_weight_kg:
            errors.append("Ingredient quantities must add up to the planned mixture weight.")
        if total_allocations > total_ingredients:
            errors.append("Total kg assigned to houses cannot be more than the kg mixed.")
        if total_weight_kg is not None and total_weight_kg > total_ingredients:
            errors.append("Total weighed kg cannot be more than the kg of ingredients mixed.")
        if total_weight_kg is not None and total_allocations > total_weight_kg:
            errors.append("Total kg assigned to houses cannot be more than the final weighed mixture.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            with transaction.atomic():
                if formula_id == "custom" and save_custom:
                    selected_formula = FeedFormulaTemplate.objects.create(
                        name=saved_formula_name,
                        flock_stage=flock_stage,
                        reference_weight_kg=target_weight_kg,
                        source="Farm custom formula",
                        is_system=False,
                        is_active=True,
                        created_by=request.user,
                    )
                    FeedFormulaIngredient.objects.bulk_create([
                        FeedFormulaIngredient(
                            formula=selected_formula,
                            item=item["item"],
                            quantity_kg=item["quantity_kg"],
                            sort_order=index,
                        )
                        for index, item in enumerate(ingredients, start=1)
                    ])

                formula_name = (
                    selected_formula.name
                    if selected_formula
                    else custom_formula_name or "Custom formula"
                )
                mixture = FeedMixture.objects.create(
                    name=name or f"{formula_name} - {_stage_label(flock_stage)}",
                    mix_date=mix_date,
                    formula_template=selected_formula,
                    formula_name_snapshot=formula_name,
                    flock_stage=flock_stage,
                    planned_weight_kg=total_ingredients,
                    total_weight_kg=total_weight_kg,
                    notes=notes,
                    mixed_by=request.user,
                )
                FeedMixtureIngredient.objects.bulk_create([
                    FeedMixtureIngredient(mixture=mixture, **item) for item in ingredients
                ])
                FeedMixtureAllocation.objects.bulk_create([
                    FeedMixtureAllocation(mixture=mixture, **item) for item in allocations
                ])
                InventoryTransaction.objects.bulk_create([
                    InventoryTransaction(
                        tx_date=mix_date,
                        tx_type=InventoryTransaction.TxType.OUT,
                        store=store,
                        item=item["item"],
                        quantity=item["quantity_kg"],
                        reference=f"MIX-{mixture.mixture_id}",
                        notes=f"Used in feed mixture {mixture.name}",
                        created_by=request.user,
                    )
                    for item in ingredients
                ])
            messages.success(request, f"Feed mixture {mixture.name} recorded and assigned.")
            return redirect("feed_mixtures")

    recent_mixtures = (
        FeedMixture.objects.select_related("mixed_by", "formula_template")
        .prefetch_related("ingredients", "allocations__house", "allocations__batch")
        .order_by("-mix_date", "-created_at")[:20]
    )

    formula_payload = []
    for formula in formula_templates:
        formula_payload.append({
            "id": str(formula.pk),
            "name": formula.name,
            "stage": formula.flock_stage,
            "stageLabel": formula.get_flock_stage_display(),
            "referenceWeight": str(formula.reference_weight_kg),
            "isSystem": formula.is_system,
            "ingredients": [
                {
                    "itemId": str(line.item_id),
                    "name": line.item.name,
                    "quantity": str(line.quantity_kg),
                    "available": str(feed_item_stock.get(line.item_id, Decimal("0.000"))),
                }
                for line in formula.ingredients.all()
            ],
        })

    batch_payload = [
        {
            "id": str(batch.pk),
            "houseId": str(batch.house_id),
            "label": f"{batch.house.house_code} - {batch.batch_code}",
            "stage": batch.formula_stage,
            "stageLabel": batch.formula_stage_label,
            "ageDays": batch.current_age_days,
        }
        for batch in batches
    ]

    return render(request, "feed_mixtures.html", {
        "today": date.today(),
        "batches": batches,
        "feed_items": feed_items,
        "feed_item_stock": feed_item_stock,
        "formula_templates": formula_templates,
        "formula_payload": formula_payload,
        "batch_payload": batch_payload,
        "custom_templates": [formula for formula in formula_templates if not formula.is_system],
        "recent_mixtures": recent_mixtures,
    })


@supervisor_required
def archive_feed_formula(request, pk):
    if request.method != "POST":
        return redirect("feed_mixtures")

    formula = get_object_or_404(FeedFormulaTemplate, pk=pk)
    role_code = _role_code(request.user)
    if formula.is_system:
        messages.error(request, "System feed formulas cannot be archived.")
    elif role_code == "SUPERVISOR" and formula.created_by_id != request.user.pk:
        messages.error(request, "You can only archive custom formulas that you created.")
    elif not formula.is_active:
        messages.info(request, "This custom formula is already archived.")
    else:
        formula.is_active = False
        formula.save(update_fields=["is_active"])
        messages.success(request, f"{formula.name} was archived. Past mixtures were not changed.")
    return redirect("feed_mixtures")


@worker_required
def record_cleaning(request):
    batches = _get_worker_active_batches(request.user)
    cleaning_tasks = [
        ("cleaned", "house_cleaned", "General House cleaning", "cleaned_photos"),
        ("raked", "house_raked", "House raked", "raked_photos"),
        ("dusted", "house_dusted", "Dusted", "dusted_photos"),
        ("nipples", "nipples_washed", "Washed the nipples", "nipples_photos"),
        ("disinfected", "disinfection_done", "Disinfected", "disinfected_photos"),
        ("water_changed", "water_changed", "Water changed", "water_changed_photos"),
    ]

    if request.method == "POST":
        batch_id = request.POST.get("batch", "").strip()
        record_date = localdate()
        selected_tasks = {
            model_field: bool(request.POST.get(post_name))
            for post_name, model_field, _label, _photo_field in cleaning_tasks
        }
        notes = request.POST.get("notes", "").strip()
        notes_audio = request.FILES.get("notes_audio")

        errors = []
        task_photos = {}
        selected_batch = _selected_or_only(batches, batch_id)

        if not selected_batch:
            errors.append("Please select a valid batch from your assigned houses.")

        if not any(selected_tasks.values()):
            errors.append("Please tick at least one cleaning task.")

        for post_name, model_field, label, photo_field in cleaning_tasks:
            photos = request.FILES.getlist(photo_field)
            task_photos[post_name] = photos
            if selected_tasks[model_field] and not photos:
                errors.append(f"Please upload or take a photo for: {label}.")
            for photo in photos:
                if not getattr(photo, "content_type", "").startswith("image/"):
                    errors.append(f"Please upload an image file for: {label}.")

        if notes_audio and not getattr(notes_audio, "content_type", "").startswith("audio/"):
            errors.append("Please upload a valid audio file for the voice note.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            cleaning = CleaningRecord.objects.create(
                batch=selected_batch,
                record_date=record_date,
                **selected_tasks,
                notes=notes,
                notes_audio=notes_audio,
                recorded_by=request.user,
            )

            for post_name, _model_field, _label, _photo_field in cleaning_tasks:
                for photo in task_photos[post_name]:
                    CleaningPhoto.objects.create(
                        cleaning_record=cleaning,
                        task_key=post_name,
                        image=photo,
                        uploaded_by=request.user,
                    )

            
            messages.success(request, "Cleaning routine record saved successfully.")
            return redirect("record_cleaning")

    recent_cleaning_records = CleaningRecord.objects.filter(
        recorded_by=request.user,
        batch__in=batches,
        record_date=localdate(),
    ).select_related("batch__house")[:10]
    # show only the most recent 7 cleaning records
    recent_cleaning_records = recent_cleaning_records[:7]

    return render(
        request,
        "record_cleaning.html",
        {
            "batches": batches,
            "today": localdate(),
            "recent_cleaning_records": recent_cleaning_records,
            "cleaning_tasks": cleaning_tasks,
        },
    )

@worker_required
def workersdash(request):
    user = request.user
    today = date.today()
    assigned_houses = user.houses.all().order_by("house_code", "name")
    house = assigned_houses.first()

    active_batches = PoultryBatch.objects.filter(
        house__in=assigned_houses,
        status="ACTIVE"
    ).select_related("house").prefetch_related("mortality_records")

    total_birds = 0
    today_deaths = 0
    house_stats = []

    for batch in active_batches:
        total_mortality = batch.mortality_records.aggregate(
            total=Sum("number_dead")
        )["total"] or 0

        batch_today_deaths = batch.mortality_records.filter(
            record_date=today
        ).aggregate(total=Sum("number_dead"))["total"] or 0

        current_birds = max(batch.initial_quantity - total_mortality, 0)
        total_birds += current_birds
        today_deaths += batch_today_deaths

        house_stats.append({
            "house": batch.house,
            "current_birds": current_birds,
            "today_deaths": batch_today_deaths,
            "age_days": batch.current_age_days,
        })

    today_eggs = egg_collection.objects.filter(
        batch__house__in=assigned_houses,
        collection_date=today
    ).aggregate(total=Sum("eggs_collected"))["total"] or 0

    recent_activity = egg_collection.objects.filter(
        collected_by=user,
        collection_date=today,
        batch__house__in=assigned_houses,
    ).select_related("batch__house").order_by("-collected_at")[:5]
    welfare_requests = WelfareRequest.objects.filter(worker=user)
    welfare_pending_count = welfare_requests.filter(
        status__in=[
            WelfareRequest.Status.SUBMITTED,
            WelfareRequest.Status.SUPERVISOR_APPROVED,
        ]
    ).count()
    recent_salary_records = SalaryPayment.objects.filter(employee=user).order_by("-period_month", "-recorded_at")[:3]

    context = {
        "house": house,
        "assigned_houses": assigned_houses,
        "total_birds": total_birds,
        "today_deaths": today_deaths,
        "today_eggs": today_eggs,
        "house_stats": house_stats,
        "recent_activity": recent_activity,
        "welfare_pending_count": welfare_pending_count,
        "recent_welfare_requests": welfare_requests[:3],
        "recent_salary_records": recent_salary_records,
        "today": today,
    }
    return render(request, "workersdash.html", context)
   

@supervisor_required
def supdash(request):
    today = date.today()
    role_code = _role_code(request.user)
    if role_code == "SUPERVISOR":
        assigned_houses = request.user.houses.filter(is_active=True).order_by("house_code", "name")
    else:
        assigned_houses = PoultryHouse.objects.filter(is_active=True).order_by("house_code", "name")

    egg_records = _scope_to_supervisor_houses(egg_collection.objects.all(), request.user, "batch__house")
    feed_records = _scope_to_supervisor_houses(FeedRecord.objects.all(), request.user, "batch__house")
    cleaning_records = _scope_to_supervisor_houses(CleaningRecord.objects.all(), request.user, "batch__house")
    mortality_records = _scope_to_supervisor_houses(MortalityRecord.objects.all(), request.user, "batch__house")
    sickness_records = _scope_to_supervisor_houses(SicknessReport.objects.all(), request.user, "house_ref")
    treatment_plan_items = _scope_to_supervisor_houses(TreatmentPlanItem.objects.all(), request.user, "sickness_report__house_ref")

    # Pending counts
    pending_eggs = egg_records.filter(status=ApprovalStatus.PENDING).count()
    pending_feed = feed_records.filter(status=ApprovalStatus.PENDING).count()
    pending_cleaning = cleaning_records.filter(status=ApprovalStatus.PENDING).count()
    pending_mortality = mortality_records.filter(status=ApprovalStatus.PENDING).count()
    total_pending = pending_eggs + pending_feed + pending_cleaning + pending_mortality

    # Today's summary (approved records only)
    today_eggs = egg_records.filter(
        collection_date=today, status=ApprovalStatus.APPROVED
    ).aggregate(total=Sum("eggs_collected"))["total"] or 0

    today_deaths = mortality_records.filter(
        record_date=today, status=ApprovalStatus.APPROVED
    ).aggregate(total=Sum("number_dead"))["total"] or 0

    today_feed_kg = feed_records.filter(
        record_date=today, status=ApprovalStatus.APPROVED
    ).aggregate(total=Sum("quantity_kg"))["total"] or 0

    houses_cleaned_today = cleaning_records.filter(
        record_date=today, status=ApprovalStatus.APPROVED, house_cleaned=True
    ).count()
    today_sickness_cases = sickness_records.filter(date=today).count()
    isolated_sickness_cases = sickness_records.filter(isolated=True).count()
    pending_treatment_doses = treatment_plan_items.filter(is_given=False).count()
    eggs_stock = _build_product_stock()["eggs"]

    # Recent pending items for quick view
    recent_pending_eggs = egg_records.filter(
        status=ApprovalStatus.PENDING
    ).select_related("batch__house", "collected_by").order_by("-collected_at")[:5]

    recent_pending_mortality = mortality_records.filter(
        status=ApprovalStatus.PENDING
    ).select_related("batch__house", "reported_by", "cause").order_by("-reported_at")[:5]
    recent_sickness_reports = (
        sickness_records.select_related("house_ref", "batch", "reported_by")
        .annotate(
            cleaning_sessions=Count("sickbay_cleanings", distinct=True),
            last_cleaned_at=Max("sickbay_cleanings__record_date"),
        )
        .order_by("-date", "-created_at")[:5]
    )
    recent_pending_treatments = (
        treatment_plan_items.filter(is_given=False)
        .select_related("sickness_report__house_ref")
        .order_by("scheduled_for", "-created_at")[:5]
    )

    # Active batches / houses overview
    active_batches = _scope_to_supervisor_houses(
        PoultryBatch.objects.filter(status=PoultryBatch.Status.ACTIVE),
        request.user,
        "house",
    ).select_related("house")
    total_birds = 0
    for batch in active_batches:
        mort = batch.mortality_records.filter(status=ApprovalStatus.APPROVED).aggregate(
            total=Sum("number_dead"))["total"] or 0
        total_birds += max(batch.initial_quantity - mort, 0)

    context = {
        "today": today,
        "assigned_houses": assigned_houses,
        "assigned_houses_count": assigned_houses.count(),
        "pending_eggs": pending_eggs,
        "pending_feed": pending_feed,
        "pending_cleaning": pending_cleaning,
        "pending_mortality": pending_mortality,
        "total_pending": total_pending,
        "today_eggs": today_eggs,
        "eggs_in_stock": eggs_stock["available_qty"],
        "eggs_in_stock_display": eggs_stock["available_display"],
        "today_deaths": today_deaths,
        "today_feed_kg": today_feed_kg,
        "houses_cleaned_today": houses_cleaned_today,
        "today_sickness_cases": today_sickness_cases,
        "isolated_sickness_cases": isolated_sickness_cases,
        "pending_treatment_doses": pending_treatment_doses,
        "recent_pending_eggs": recent_pending_eggs,
        "recent_pending_mortality": recent_pending_mortality,
        "recent_sickness_reports": recent_sickness_reports,
        "recent_pending_treatments": recent_pending_treatments,
        "total_birds": total_birds,
        "active_batches_count": active_batches.count(),
    }
    return render(request, 'supdash.html', context)


@supervisor_required
def sup_approve_eggs(request, pk):
    record = get_object_or_404(
        _scope_to_supervisor_houses(egg_collection.objects.all(), request.user, "batch__house"),
        pk=pk,
    )
    action = request.POST.get("action")
    review_notes = request.POST.get("review_notes", "").strip()
    if action == "approve":
        record.status = ApprovalStatus.APPROVED
        messages.success(request, f"Egg collection record approved.")
    elif action == "reject":
        record.status = ApprovalStatus.REJECTED
        messages.warning(request, f"Egg collection record rejected.")
    record.reviewed_by = request.user
    record.reviewed_at = now()
    record.review_notes = review_notes
    record.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes"])
    return redirect("supapproval")


@supervisor_required
def sup_approve_feed(request, pk):
    record = get_object_or_404(
        _scope_to_supervisor_houses(FeedRecord.objects.all(), request.user, "batch__house"),
        pk=pk,
    )
    action = request.POST.get("action")
    review_notes = request.POST.get("review_notes", "").strip()
    if action == "approve":
        record.status = ApprovalStatus.APPROVED
        messages.success(request, f"Feed record approved.")
    elif action == "reject":
        record.status = ApprovalStatus.REJECTED
        messages.warning(request, f"Feed record rejected.")
    record.reviewed_by = request.user
    record.reviewed_at = now()
    record.review_notes = review_notes
    record.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes"])
    return redirect("supapproval")


@supervisor_required
def sup_approve_cleaning(request, pk):
    record = get_object_or_404(
        _scope_to_supervisor_houses(CleaningRecord.objects.all(), request.user, "batch__house"),
        pk=pk,
    )
    action = request.POST.get("action")
    review_notes = request.POST.get("review_notes", "").strip()
    if action == "approve":
        record.status = ApprovalStatus.APPROVED
        messages.success(request, f"Cleaning record approved.")
    elif action == "reject":
        record.status = ApprovalStatus.REJECTED
        messages.warning(request, f"Cleaning record rejected.")
    record.reviewed_by = request.user
    record.reviewed_at = now()
    record.review_notes = review_notes
    record.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes"])
    return redirect("supapproval")


@supervisor_required
def sup_approve_mortality(request, pk):
    record = get_object_or_404(
        _scope_to_supervisor_houses(MortalityRecord.objects.all(), request.user, "batch__house"),
        pk=pk,
    )
    action = request.POST.get("action")
    review_notes = request.POST.get("review_notes", "").strip()
    if action == "approve":
        record.status = ApprovalStatus.APPROVED
        messages.success(request, f"Mortality record approved.")
    elif action == "reject":
        record.status = ApprovalStatus.REJECTED
        messages.warning(request, f"Mortality record rejected.")
    record.reviewed_by = request.user
    record.reviewed_at = now()
    record.review_notes = review_notes
    record.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes"])
    return redirect("supapproval")


@supervisor_required
def supapproval(request):
    tab = request.GET.get("tab", "eggs")
    role_code = _role_code(request.user)
    if role_code == "SUPERVISOR":
        assigned_houses = request.user.houses.filter(is_active=True).order_by("house_code", "name")
    else:
        assigned_houses = PoultryHouse.objects.filter(is_active=True).order_by("house_code", "name")

    pending_eggs = _scope_to_supervisor_houses(
        egg_collection.objects.filter(
        status=ApprovalStatus.PENDING
        ),
        request.user,
        "batch__house",
    ).select_related("batch__house", "collected_by", "sickness_report").order_by("-collected_at")

    pending_feed = _scope_to_supervisor_houses(
        FeedRecord.objects.filter(
        status=ApprovalStatus.PENDING
        ),
        request.user,
        "batch__house",
    ).select_related("batch__house", "recorded_by", "sickness_report", "feed_mixture").order_by("-created_at")

    pending_cleaning = _scope_to_supervisor_houses(
        CleaningRecord.objects.filter(
        status=ApprovalStatus.PENDING
        ),
        request.user,
        "batch__house",
    ).select_related("batch__house", "recorded_by", "sickness_report").prefetch_related("photos").order_by("-created_at")

    pending_mortality = _scope_to_supervisor_houses(
        MortalityRecord.objects.filter(
        status=ApprovalStatus.PENDING
        ),
        request.user,
        "batch__house",
    ).select_related("batch__house", "reported_by", "cause").order_by("-reported_at")

    pending_eggs_count = pending_eggs.count()
    pending_feed_count = pending_feed.count()
    pending_cleaning_count = pending_cleaning.count()
    pending_mortality_count = pending_mortality.count()

    page_obj = None
    querystring = ""
    if tab == "eggs":
        page_obj, querystring = paginate(request, pending_eggs, per_page=20)
        pending_eggs = page_obj
    elif tab == "feed":
        page_obj, querystring = paginate(request, pending_feed, per_page=20)
        pending_feed = page_obj
    elif tab == "cleaning":
        page_obj, querystring = paginate(request, pending_cleaning, per_page=20)
        pending_cleaning = page_obj
    elif tab == "mortality":
        page_obj, querystring = paginate(request, pending_mortality, per_page=20)
        pending_mortality = page_obj

    context = {
        "tab": tab,
        "assigned_houses": assigned_houses,
        "assigned_houses_count": assigned_houses.count(),
        "pending_eggs": pending_eggs,
        "pending_feed": pending_feed,
        "pending_cleaning": pending_cleaning,
        "pending_mortality": pending_mortality,
        "page_obj": page_obj,
        "querystring": querystring,
        "pending_eggs_count": pending_eggs_count,
        "pending_feed_count": pending_feed_count,
        "pending_cleaning_count": pending_cleaning_count,
        "pending_mortality_count": pending_mortality_count,
    }
    return render(request, 'supapproval.html', context)


def login(request):
    return render(request, 'login.html')

def eggrec(request):
    records = egg_collection.objects.select_related(
        "batch__house", "collected_by", "reviewed_by"
    ).filter(sickness_report__isnull=True)

    # filters
    f_date = request.GET.get("date", "").strip()
    f_house = request.GET.get("house", "").strip()
    f_status = request.GET.get("status", "").strip()
    f_breed = request.GET.get("breed", "").strip()

    if f_date:
        try:
            records = records.filter(collection_date=date.fromisoformat(f_date))
        except ValueError:
            pass
    if f_house:
        records = records.filter(batch__house__pk=f_house)
    if f_status:
        records = records.filter(status=f_status)
    if f_breed:
        records = records.filter(batch__breed__icontains=f_breed)

    records = records.order_by("-collection_date", "-collection_id")
    page_obj, querystring = paginate(request, records, per_page=20)
    houses = PoultryHouse.objects.filter(is_active=True).order_by("house_code")
    breeds = (
        PoultryBatch.objects.exclude(breed="")
        .values_list("breed", flat=True)
        .distinct()
        .order_by("breed")
    )

    return render(request, 'eggrec.html', {
        "records": page_obj,
        "page_obj": page_obj,
        "querystring": querystring,
        "houses": houses,
        "breeds": breeds,
        "f_date": f_date,
        "f_house": f_house,
        "f_status": f_status,
        "f_breed": f_breed,
        "approval_statuses": ApprovalStatus.choices,
    })

@login_required(login_url="login")
def feedrec(request):
    role_code = _role_code(request.user)
    show_feed_records = role_code in {"MANAGER", "OWNER"}

    recent_mixtures = (
        FeedMixture.objects.select_related("mixed_by", "formula_template")
        .prefetch_related("ingredients", "allocations__house", "allocations__batch")
        .order_by("-mix_date", "-created_at")
    )
    mixtures_page_obj, mixtures_querystring = paginate(
        request,
        recent_mixtures,
        per_page=8,
        page_param="mixtures_page",
    )

    feed_records_page_obj = None
    feed_records_querystring = ""
    if show_feed_records:
        feed_records = (
            FeedRecord.objects.select_related(
                "batch",
                "batch__house",
                "feed_mixture",
                "recorded_by",
                "sickness_report",
            )
            .order_by("-record_date", "-created_at", "-feed_id")
        )
        feed_records_page_obj, feed_records_querystring = paginate(
            request,
            feed_records,
            per_page=15,
            page_param="feed_page",
        )

    return render(request, 'feed_rec.html', {
        "recent_mixtures": mixtures_page_obj,
        "mixtures_page_obj": mixtures_page_obj,
        "mixtures_querystring": mixtures_querystring,
        "feed_records": feed_records_page_obj,
        "feed_records_page_obj": feed_records_page_obj,
        "feed_records_querystring": feed_records_querystring,
        "show_feed_records": show_feed_records,
    })


def _hydrate_legacy_analysis_metrics(report, start_date, end_date, group_by):
    unresolved = report.get("unresolvedMetricKeys") or []
    if not unresolved:
        return report

    previous_range = report["meta"]["previousRange"]
    previous_start = date.fromisoformat(previous_range["start"])
    previous_end = date.fromisoformat(previous_range["end"])
    current_data = _build_investor_builder_data(start_date, end_date, group_by)
    previous_data = _build_investor_builder_data(previous_start, previous_end, group_by)
    current_by_key = {item["key"]: item for item in current_data["indicators"]}
    previous_by_key = {item["key"]: item for item in previous_data["indicators"]}
    dimension = report["meta"]["dimension"]

    for key in unresolved:
        metadata = METRIC_CATALOG[key]
        current = current_by_key.get(key)
        if current is None:
            continue
        previous = previous_by_key.get(key) if metadata["comparisonSupported"] else None
        value = current.get("summary")
        previous_value = previous.get("summary") if previous else None
        absolute_change = None
        percent_change = None
        variance_state = "unavailable"
        if value is not None and previous_value is not None:
            absolute_change = value - previous_value
            if previous_value:
                percent_change = absolute_change / abs(previous_value) * 100
            if absolute_change == 0 or metadata["performanceDirection"] == "neutral":
                variance_state = "neutral"
            elif metadata["performanceDirection"] == "higher":
                variance_state = "favorable" if absolute_change > 0 else "unfavorable"
            else:
                variance_state = "favorable" if absolute_change < 0 else "unfavorable"
        points = current.get("dimensions", {}).get(
            dimension,
            [{"label": "Selected period", "value": value}],
        )
        metric = {
            **metadata,
            "value": value,
            "points": points,
            "records": None,
            "hasData": any(point.get("value") not in (None, 0) for point in points),
            "previousValue": previous_value,
            "absoluteChange": absolute_change,
            "percentChange": percent_change,
            "varianceState": variance_state,
            "target": target_payload(key, value, None),
        }
        report["metrics"].append(metric)
        section = next(
            (item for item in report["sections"] if item["key"] == metadata["unitGroup"]),
            None,
        )
        if section:
            section["metricKeys"].append(key)
        else:
            report["sections"].append({
                "key": metadata["unitGroup"],
                "title": "Additional measures",
                "metricKeys": [key],
            })
    report["unresolvedMetricKeys"] = []
    report["meta"]["usedLegacyCompatibility"] = True
    return report


@login_required(login_url="login")
@require_POST
def investor_analysis(request):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON request."}, status=400)

    mode = payload.get("mode", "guided")
    if mode not in {"guided", "analyst"}:
        return JsonResponse({"error": "Mode must be guided or analyst."}, status=400)
    question_key = payload.get("questionKey", "")
    if mode == "guided" and question_key not in GUIDED_QUESTIONS:
        return JsonResponse({"error": "Select a valid investor question."}, status=400)

    metric_keys = payload.get("metricKeys") or []
    if mode == "analyst":
        if not isinstance(metric_keys, list) or not 1 <= len(metric_keys) <= 6:
            return JsonResponse({"error": "Analyst reports require one to six indicators."}, status=400)
        if len(metric_keys) != len(set(metric_keys)) or any(key not in METRIC_CATALOG for key in metric_keys):
            return JsonResponse({"error": "One or more indicators are invalid or duplicated."}, status=400)

    try:
        start_date = date.fromisoformat(payload.get("startDate", ""))
        end_date = date.fromisoformat(payload.get("endDate", ""))
    except (TypeError, ValueError):
        return JsonResponse({"error": "Provide valid start and end dates."}, status=400)
    if start_date > end_date:
        return JsonResponse({"error": "Start date cannot be after end date."}, status=400)
    if (end_date - start_date).days > 1095:
        return JsonResponse({"error": "Analysis ranges cannot exceed three years."}, status=400)

    group_by = payload.get("groupBy", "month")
    if group_by not in {"day", "week", "month"}:
        return JsonResponse({"error": "Grouping must be day, week, or month."}, status=400)
    scope_type = payload.get("scopeType", "farm")
    scope_id = payload.get("scopeId")
    if scope_type not in {"farm", "house", "batch"}:
        return JsonResponse({"error": "Scope must be farm, house, or batch."}, status=400)
    if scope_type == "house" and not PoultryHouse.objects.filter(pk=scope_id).exists():
        return JsonResponse({"error": "Select a valid poultry house."}, status=400)
    if scope_type == "batch" and not PoultryBatch.objects.filter(pk=scope_id).exists():
        return JsonResponse({"error": "Select a valid poultry batch."}, status=400)
    if scope_type == "farm":
        scope_id = None

    dimension = payload.get("dimension", "period")
    if dimension not in INVESTOR_DIMENSIONS:
        return JsonResponse({"error": "Select a valid breakdown."}, status=400)
    if mode == "analyst":
        supported = [set(METRIC_CATALOG[key]["dimensions"]) for key in metric_keys]
        common_dimensions = set.intersection(*supported)
        if dimension not in common_dimensions:
            return JsonResponse(
                {"error": "The selected indicators do not all support this breakdown."},
                status=400,
            )

    report = build_report(
        mode=mode,
        question_key=question_key,
        metric_keys=metric_keys,
        start_date=start_date,
        end_date=end_date,
        group_by=group_by,
        scope_type=scope_type,
        scope_id=scope_id,
        dimension=dimension,
        requested_view=payload.get("view", "bar"),
    )
    report = _hydrate_legacy_analysis_metrics(report, start_date, end_date, group_by)
    return JsonResponse(report)


@login_required(login_url="login")
@require_http_methods(["GET", "POST"])
def investor_targets(request):
    if request.method == "GET":
        targets = InvestorKpiTarget.objects.select_related("created_by").order_by(
            "metric_key", "-effective_from"
        )
        return JsonResponse({
            "canEdit": request.user.is_investor,
            "targets": [
                {
                    "id": target.pk,
                    "metricKey": target.metric_key,
                    "metricLabel": METRIC_CATALOG.get(target.metric_key, {}).get("label", target.metric_key),
                    "value": float(target.target_value),
                    "direction": target.direction,
                    "effectiveFrom": target.effective_from.isoformat(),
                    "effectiveTo": target.effective_to.isoformat() if target.effective_to else None,
                    "createdBy": target.created_by.display_name,
                    "createdAt": target.created_at.isoformat(),
                }
                for target in targets
            ],
        })

    if not request.user.is_investor:
        return JsonResponse({"error": "Only the owner can change investor KPI targets."}, status=403)
    try:
        payload = json.loads(request.body or "{}")
        metric_key = payload.get("metricKey", "")
        target_value = Decimal(str(payload.get("targetValue", "")))
        effective_from = date.fromisoformat(payload.get("effectiveFrom", ""))
    except (json.JSONDecodeError, InvalidOperation, TypeError, ValueError):
        return JsonResponse({"error": "Provide a valid indicator, target value, and effective date."}, status=400)

    try:
        with transaction.atomic():
            target = create_target_version(
                metric_key=metric_key,
                target_value=target_value,
                effective_from=effective_from,
                user=request.user,
            )
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)
    return JsonResponse({
        "target": {
            "id": target.pk,
            "metricKey": target.metric_key,
            "value": float(target.target_value),
            "direction": target.direction,
            "effectiveFrom": target.effective_from.isoformat(),
            "effectiveTo": target.effective_to.isoformat() if target.effective_to else None,
        }
    }, status=201)


@login_required(login_url="login")
def investor(request):
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

    selected_report_type = request.GET.get("report_type", "profit")
    selected_group_by = request.GET.get("group_by", "month")
    selected_chart_type = request.GET.get("chart_type", "line")

    financial_context = _build_investor_financial_context(start_date, end_date, selected_group_by)

    context = {
        "today": today,
        "start_date": start_date,
        "end_date": end_date,
        "selected_report_type": selected_report_type,
        "selected_group_by": selected_group_by,
        "selected_chart_type": selected_chart_type,
        "builder_catalog": catalogue_payload(),
        "builder_houses": PoultryHouse.objects.order_by("house_code"),
        "builder_batches": PoultryBatch.objects.select_related("house").order_by("-date_stocked", "batch_code"),
    }
    context.update(financial_context)
    return render(request, 'investor.html', context)

@login_required(login_url="login")
def add_batch(request):
    if request.method == "POST":
        form = PoultryBatchForm(request.POST)
        if form.is_valid():
            batch = form.save(commit=False)
            batch.created_by = request.user
            batch.save()

            if batch.amount_paid and batch.amount_paid > 0:
                batch_expense_category, _ = ExpenseCategory.objects.get_or_create(
                    code="BATCH_PURCHASE",
                    defaults={
                        "name": "Bird Batch Purchase",
                        "expense_type": ExpenseCategory.ExpenseType.COST_OF_REVENUE,
                        "is_active": True,
                    },
                )
                if batch_expense_category.expense_type != ExpenseCategory.ExpenseType.COST_OF_REVENUE:
                    batch_expense_category.expense_type = ExpenseCategory.ExpenseType.COST_OF_REVENUE
                    batch_expense_category.is_active = True
                    batch_expense_category.save(update_fields=["expense_type", "is_active"])

                if not batch_expense_category.account_id:
                    messages.error(request, "Bird Batch Purchase is not mapped to a cost account. Please map it in Expense Categories.")
                    return redirect("birds")

                expense = ExpenseTransaction.objects.create(
                    expense_date=batch.date_stocked,
                    category=batch_expense_category,
                    description=(
                        f"Bird batch purchase {batch.batch_code} - "
                        f"{batch.initial_quantity} birds"
                        + (f" from {batch.supplier_name}" if batch.supplier_name else "")
                    ),
                    total_amount=batch.amount_paid,
                    payment_method=ExpenseTransaction.PAYMENT_CASH,
                    period_year=batch.date_stocked.year,
                    period_month=batch.date_stocked.month,
                    status=ExpenseTransaction.Status.DRAFT,
                    created_by=request.user,
                )
                AccountingCode.create_for(
                    account=batch_expense_category.account,
                    content_object=expense,
                    description="Bird batch purchase",
                )
                post_expense(expense, created_by=request.user)

            messages.success(request, f"Batch {batch.batch_code} created successfully!")
            return redirect("birds")
    else:
        form = PoultryBatchForm()

    return render(request, "addbatch.html", {"form": form})
