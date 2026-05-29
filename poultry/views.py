from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, DecimalField, Max, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils.timezone import now
from datetime import date, timedelta
from datetime import datetime
from decimal import Decimal, InvalidOperation

from accounts.decorators import worker_required, supervisor_required
from accounts.models import User
from alerts.models import Alert
from finance.models import ExpenseCategory, ExpenseTransaction, SalaryPayment
from health.models import SicknessReport, TreatmentPlanItem, VaccinationSchedule
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
    FeedMixture,
    FeedMixtureAllocation,
    FeedMixtureIngredient,
    CleaningRecord,
    CleaningPhoto,
    MortalityRecord,
    ApprovalStatus,
)
from .forms import PoultryBatchForm


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
        "pending_salaries": SalaryPayment.objects.filter(status=SalaryPayment.Status.PENDING).count(),
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
            allocations__house__in=batches.values("house"),
        )
        .select_related("mixed_by")
        .prefetch_related("allocations__house", "ingredients")
        .distinct()
        .order_by("-created_at")
    )


def _stock_for_item(item):
    transactions = InventoryTransaction.objects.filter(item=item)
    stock_in = transactions.filter(tx_type=InventoryTransaction.TxType.IN_).aggregate(total=Sum("quantity"))["total"] or Decimal("0.000")
    stock_out = transactions.filter(tx_type=InventoryTransaction.TxType.OUT).aggregate(total=Sum("quantity"))["total"] or Decimal("0.000")
    adjustments = transactions.filter(tx_type=InventoryTransaction.TxType.ADJUST).aggregate(total=Sum("quantity"))["total"] or Decimal("0.000")
    return stock_in - stock_out + adjustments


@worker_required
def record_feed(request):
    batches = _get_worker_active_batches(request.user)
    available_mixtures = _mixtures_for_worker_batches(batches)

    if request.method == "POST":
        batch_id = request.POST.get("batch", "").strip()
        record_date_raw = request.POST.get("record_date", "").strip()
        feed_mixture_id = request.POST.get("feed_mixture", "").strip()
        quantity_raw = request.POST.get("quantity", "").strip()
        time_given_raw = request.POST.get("time_given", "").strip()
        notes = request.POST.get("notes", "").strip()
        photos = request.FILES.getlist('photos')

        errors = []
        selected_batch = batches.filter(pk=batch_id).first() if batch_id else None
        selected_mixture = None

        if not selected_batch:
            errors.append("Please select a valid batch from your assigned houses.")
        elif feed_mixture_id:
            selected_mixture = available_mixtures.filter(
                pk=feed_mixture_id,
                allocations__house=selected_batch.house,
            ).first()
            if not selected_mixture:
                errors.append("Please select a mixture assigned to this house.")
        else:
            errors.append("Please select a supervisor feed mixture.")

        try:
            record_date = date.fromisoformat(record_date_raw)
        except ValueError:
            record_date = None
            errors.append("Please provide a valid date.")

        # enforce only today's records
        if record_date and record_date != date.today():
            errors.append("Feed records can only be created for today.")

        try:
            quantity_kg = Decimal(quantity_raw)
            if quantity_kg <= 0:
                errors.append("Quantity must be greater than zero.")
        except (InvalidOperation, ValueError):
            quantity_kg = None
            errors.append("Please provide a valid quantity.")

        if selected_mixture and selected_batch and quantity_kg is not None:
            allocation = selected_mixture.allocations.filter(house=selected_batch.house).first()
            allocated_kg = allocation.quantity_kg if allocation else Decimal("0.00")
            already_recorded_kg = FeedRecord.objects.filter(
                feed_mixture=selected_mixture,
                batch__house=selected_batch.house,
                record_date=record_date or date.today(),
            ).exclude(status=ApprovalStatus.REJECTED).aggregate(total=Sum("quantity_kg"))["total"] or Decimal("0.00")
            if already_recorded_kg + quantity_kg > allocated_kg:
                remaining_kg = max(allocated_kg - already_recorded_kg, Decimal("0.00"))
                errors.append(
                    f"This house has {remaining_kg} kg remaining from {selected_mixture.name}."
                )

        parsed_time = None
        if time_given_raw:
            try:
                parsed_time = datetime.strptime(time_given_raw, "%H:%M").time()
            except ValueError:
                errors.append("Please provide a valid time.")

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
        record_date=date.today(),
    ).select_related("batch__house", "feed_mixture")[:10]

    return render(
        request,
        "record_feed.html",
        {
            "batches": batches,
            "today": date.today(),
            "recent_feed_records": recent_feed_records,
            "feed_mixtures": available_mixtures,
        },
    )

@worker_required
def record_egg(request):
    batches = _get_worker_active_batches(request.user)

    if request.method == "POST":
        batch_id = request.POST.get("batch", "").strip()
        collection_date_raw = request.POST.get("collection_date", "").strip()
        total_eggs_raw = request.POST.get("total_eggs", "").strip()
        broken_eggs_raw = request.POST.get("broken_eggs", "0").strip()
        egg_weight_values = request.POST.getlist("egg_weights")
        notes = request.POST.get("notes", "").strip()

        errors = []
        selected_batch = batches.filter(pk=batch_id).first() if batch_id else None
        average_egg_weight_g = None

        if not selected_batch:
            errors.append("Please select a valid batch from your assigned houses.")

        try:
            collection_date = date.fromisoformat(collection_date_raw)
        except ValueError:
            collection_date = None
            errors.append("Please provide a valid collection date.")

        # enforce only today's collections
        if collection_date and collection_date != date.today():
            errors.append("Egg collection records can only be created for today.")

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
        collection_date=date.today(),
    ).select_related("batch__house")[:10]

    return render(
        request,
        "record_egg.html",
        {
            "batches": batches,
            "today": date.today(),
            "recent_egg_records": recent_egg_records,
        },
    )


@supervisor_required
def feed_mixtures(request):
    houses = PoultryHouse.objects.filter(is_active=True).order_by("house_code")
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

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        mix_date_raw = request.POST.get("mix_date", "").strip()
        total_weight_raw = request.POST.get("total_weight_kg", "").strip()
        notes = request.POST.get("notes", "").strip()
        ingredient_items = request.POST.getlist("ingredient_item")
        ingredient_quantities = request.POST.getlist("ingredient_quantity")
        allocation_houses = request.POST.getlist("allocation_house")
        allocation_quantities = request.POST.getlist("allocation_quantity")

        errors = []
        ingredients = []
        allocations = []
        ingredient_totals_by_item = {}
        total_weight_kg = None

        if not name:
            errors.append("Please name this mixture.")

        try:
            mix_date = date.fromisoformat(mix_date_raw)
        except ValueError:
            mix_date = None
            errors.append("Please provide a valid mixture date.")

        if mix_date and mix_date != date.today():
            errors.append("Feed mixtures can only be recorded for today.")

        try:
            total_weight_kg = Decimal(total_weight_raw)
            if total_weight_kg <= 0:
                errors.append("Total weighed kg must be greater than zero.")
        except (InvalidOperation, ValueError):
            errors.append("Please enter the total weighed kg after mixing.")

        for item_id, quantity_raw in zip(ingredient_items, ingredient_quantities):
            quantity_raw = quantity_raw.strip()
            if not item_id and not quantity_raw:
                continue
            item = Item.objects.filter(pk=item_id, is_active=True, category__code__iexact="FEED").first()
            if not item:
                errors.append("Please select a valid feed stock item.")
                continue
            try:
                quantity_kg = Decimal(quantity_raw)
                if quantity_kg <= 0:
                    errors.append("Ingredient kg must be greater than zero.")
                    continue
            except (InvalidOperation, ValueError):
                errors.append("Please enter valid ingredient kg values.")
                continue
            available_kg = feed_item_stock.get(item.pk, Decimal("0.000"))
            if quantity_kg > available_kg:
                errors.append(f"Only {available_kg} kg of {item.name} is available in stock.")
            ingredient_totals_by_item[item.pk] = ingredient_totals_by_item.get(item.pk, Decimal("0.00")) + quantity_kg
            ingredients.append({
                "item": item,
                "feed_type": FeedRecord.FeedType.OTHER,
                "ingredient_name": item.name,
                "quantity_kg": quantity_kg,
            })

        for house_id, quantity_raw in zip(allocation_houses, allocation_quantities):
            quantity_raw = quantity_raw.strip()
            if not house_id or not quantity_raw:
                continue
            house = houses.filter(pk=house_id).first()
            if not house:
                errors.append("Please select valid houses for distribution.")
                continue
            try:
                quantity_kg = Decimal(quantity_raw)
                if quantity_kg <= 0:
                    errors.append("House allocation kg must be greater than zero.")
                    continue
            except (InvalidOperation, ValueError):
                errors.append("Please enter valid house allocation kg values.")
                continue
            allocations.append({"house": house, "quantity_kg": quantity_kg})

        allocation_house_ids = [item["house"].pk for item in allocations]
        if len(allocation_house_ids) != len(set(allocation_house_ids)):
            errors.append("Each house should appear only once in the distribution list.")

        if not ingredients:
            errors.append("Add at least one feed ingredient.")
        if not allocations:
            errors.append("Assign the mixture to at least one house.")
        for item in feed_items:
            requested_kg = ingredient_totals_by_item.get(item.pk, Decimal("0.00"))
            available_kg = feed_item_stock.get(item.pk, Decimal("0.000"))
            if requested_kg > available_kg:
                errors.append(f"Total {item.name} used is {requested_kg} kg, but only {available_kg} kg is available.")

        total_ingredients = sum((item["quantity_kg"] for item in ingredients), Decimal("0.00"))
        total_allocations = sum((item["quantity_kg"] for item in allocations), Decimal("0.00"))
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
                mixture = FeedMixture.objects.create(
                    name=name,
                    mix_date=mix_date,
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
        FeedMixture.objects.select_related("mixed_by")
        .prefetch_related("ingredients", "allocations__house")
        .order_by("-mix_date", "-created_at")[:20]
    )

    return render(request, "feed_mixtures.html", {
        "today": date.today(),
        "houses": houses,
        "feed_items": feed_items,
        "feed_item_stock": feed_item_stock,
        "recent_mixtures": recent_mixtures,
    })


@worker_required
def record_cleaning(request):
    batches = _get_worker_active_batches(request.user)

    if request.method == "POST":
        batch_id = request.POST.get("batch", "").strip()
        record_date_raw = request.POST.get("record_date", "").strip()
        house_cleaned = bool(request.POST.get("cleaned"))
        disinfection_done = bool(request.POST.get("disinfected"))
        water_changed = bool(request.POST.get("water_changed"))
        notes = request.POST.get("notes", "").strip()
        photos = request.FILES.getlist('photos')

        errors = []
        selected_batch = batches.filter(pk=batch_id).first() if batch_id else None

        if not selected_batch:
            errors.append("Please select a valid batch from your assigned houses.")

        try:
            record_date = date.fromisoformat(record_date_raw)
        except ValueError:
            record_date = None
            errors.append("Please provide a valid date.")

        # enforce only today's cleaning records
        if record_date and record_date != date.today():
            errors.append("Cleaning records can only be created for today.")

        if not any([house_cleaned, disinfection_done, water_changed]):
            errors.append("Please tick at least one cleaning task.")

        # require at least one uploaded photo as proof
        if not photos:
            errors.append("Please upload at least one photo as proof of cleaning.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            cleaning = CleaningRecord.objects.create(
                batch=selected_batch,
                record_date=record_date,
                house_cleaned=house_cleaned,
                disinfection_done=disinfection_done,
                water_changed=water_changed,
                notes=notes,
                recorded_by=request.user,
            )

            for photo in photos:
                CleaningPhoto.objects.create(
                    cleaning_record=cleaning,
                    image=photo,
                    uploaded_by=request.user,
                )

            
            messages.success(request, "Cleaning routine record saved successfully.")
            return redirect("record_cleaning")

    recent_cleaning_records = CleaningRecord.objects.filter(
        recorded_by=request.user,
        batch__in=batches,
        record_date=date.today(),
    ).select_related("batch__house")[:10]
    # show only the most recent 7 cleaning records
    recent_cleaning_records = recent_cleaning_records[:7]

    return render(
        request,
        "record_cleaning.html",
        {
            "batches": batches,
            "today": date.today(),
            "recent_cleaning_records": recent_cleaning_records,
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

    context = {
        "house": house,
        "assigned_houses": assigned_houses,
        "total_birds": total_birds,
        "today_deaths": today_deaths,
        "today_eggs": today_eggs,
        "house_stats": house_stats,
        "recent_activity": recent_activity,
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
    ).select_related("batch__house", "recorded_by", "sickness_report").order_by("-created_at")

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
        FeedMixture.objects.select_related("mixed_by")
        .prefetch_related("ingredients", "allocations__house")
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
                label = f"Week of {key_date.strftime('%d %b %Y')}"
            elif group_by == "month":
                label = current.strftime("%b %Y")
            else:
                label = current.strftime("%d %b %Y")
            grouped.setdefault(label, Decimal("0"))
            current += timedelta(days=1)

        for row in queryset.values(date_field).annotate(total=Sum(value_field)).order_by(date_field):
            row_date = row[date_field]
            if not row_date:
                continue
            if group_by == "week":
                key_date = row_date - timedelta(days=row_date.weekday())
                label = f"Week of {key_date.strftime('%d %b %Y')}"
            elif group_by == "month":
                label = row_date.strftime("%b %Y")
            else:
                label = row_date.strftime("%d %b %Y")
            grouped[label] = grouped.get(label, Decimal("0")) + (row["total"] or Decimal("0"))

        return {
            "labels": list(grouped.keys()),
            "values": [number(value) for value in grouped.values()],
        }

    sales_qs = SaleInvoice.objects.exclude(
        status=SaleInvoice.Status.CANCELLED
    ).filter(invoice_date__range=(start_date, end_date))
    payments_qs = CustomerPayment.objects.filter(payment_date__range=(start_date, end_date))
    expenses_qs = ExpenseTransaction.objects.exclude(
        status=ExpenseTransaction.Status.REJECTED
    ).filter(expense_date__range=(start_date, end_date))
    eggs_qs = egg_collection.objects.filter(
        collection_date__range=(start_date, end_date),
        status=ApprovalStatus.APPROVED,
    )
    mortality_qs = MortalityRecord.objects.filter(
        record_date__range=(start_date, end_date),
        status=ApprovalStatus.APPROVED,
    )
    feed_qs = FeedRecord.objects.filter(
        record_date__range=(start_date, end_date),
        status=ApprovalStatus.APPROVED,
    )

    total_revenue = decimal_total(sales_qs, "total_amount")
    total_cash_received = decimal_total(payments_qs, "amount")
    total_expenses = decimal_total(expenses_qs, "total_amount")
    total_profit = total_revenue - total_expenses
    total_eggs = eggs_qs.aggregate(total=Sum("eggs_collected"))["total"] or 0
    total_rejected_eggs = eggs_qs.aggregate(total=Sum("eggs_rejected"))["total"] or 0
    total_deaths = mortality_qs.aggregate(total=Sum("number_dead"))["total"] or 0
    total_feed_kg = feed_qs.aggregate(total=Sum("quantity_kg"))["total"] or Decimal("0")
    outstanding_balance = ReceivableLedger.objects.aggregate(
        total=Coalesce(
            Sum("balance"),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    )["total"]

    active_batches = PoultryBatch.objects.filter(status=PoultryBatch.Status.ACTIVE).select_related("house")
    current_birds = 0
    for batch in active_batches:
        approved_deaths = batch.mortality_records.filter(
            status=ApprovalStatus.APPROVED
        ).aggregate(total=Sum("number_dead"))["total"] or 0
        birds_sold = SaleItem.objects.filter(batch=batch).filter(
            Q(product_name__icontains="bird") | Q(product_name__icontains="off layer")
        ).aggregate(total=Sum("quantity"))["total"] or 0
        current_birds += max(batch.initial_quantity - approved_deaths - int(birds_sold), 0)

    expense_ratio = (total_expenses / total_revenue * 100) if total_revenue else Decimal("0")
    profit_margin = (total_profit / total_revenue * 100) if total_revenue else Decimal("0")
    collection_rate = (total_cash_received / total_revenue * 100) if total_revenue else Decimal("0")
    egg_yield = (Decimal(total_eggs) / Decimal(current_birds)) if current_birds else Decimal("0")
    feed_per_egg = (total_feed_kg / Decimal(total_eggs)) if total_eggs else Decimal("0")
    mortality_rate = (Decimal(total_deaths) / Decimal(current_birds + total_deaths) * 100) if (current_birds + total_deaths) else Decimal("0")

    expense_breakdown = [
        {"label": row["category__name"] or "Uncategorised", "value": number(row["total"])}
        for row in expenses_qs.values("category__name").annotate(total=Sum("total_amount")).order_by("-total")[:8]
    ]
    sales_breakdown = [
        {"label": row["product_name"] or "Sales", "value": number(row["total"])}
        for row in SaleItem.objects.filter(
            invoice__in=sales_qs
        ).values("product_name").annotate(total=Sum("line_total")).order_by("-total")[:8]
    ]
    payment_breakdown = [
        {"label": row["method"] or "Unknown", "value": number(row["total"])}
        for row in payments_qs.values("method").annotate(total=Sum("amount")).order_by("-total")
    ]

    chart_data = {
        "profit": {
            "title": "Profit and loss",
            "unit": "UGX",
            "series": [
                {"label": "Revenue", **grouped_series(sales_qs, "invoice_date", "total_amount", selected_group_by, start_date, end_date)},
                {"label": "Expenses", **grouped_series(expenses_qs, "expense_date", "total_amount", selected_group_by, start_date, end_date)},
            ],
        },
        "sales": {
            "title": "Sales revenue",
            "unit": "UGX",
            "series": [
                {"label": "Sales", **grouped_series(sales_qs, "invoice_date", "total_amount", selected_group_by, start_date, end_date)},
                {"label": "Cash received", **grouped_series(payments_qs, "payment_date", "amount", selected_group_by, start_date, end_date)},
            ],
            "pie": sales_breakdown,
        },
        "expenses": {
            "title": "Expense movement",
            "unit": "UGX",
            "series": [
                {"label": "Expenses", **grouped_series(expenses_qs, "expense_date", "total_amount", selected_group_by, start_date, end_date)},
            ],
            "pie": expense_breakdown,
        },
        "eggs": {
            "title": "Egg production",
            "unit": "eggs",
            "series": [
                {"label": "Collected eggs", **grouped_series(eggs_qs, "collection_date", "eggs_collected", selected_group_by, start_date, end_date)},
                {"label": "Rejected eggs", **grouped_series(eggs_qs, "collection_date", "eggs_rejected", selected_group_by, start_date, end_date)},
            ],
        },
        "health": {
            "title": "Mortality and feed",
            "unit": "count / kg",
            "series": [
                {"label": "Deaths", **grouped_series(mortality_qs, "record_date", "number_dead", selected_group_by, start_date, end_date)},
                {"label": "Feed used kg", **grouped_series(feed_qs, "record_date", "quantity_kg", selected_group_by, start_date, end_date)},
            ],
        },
        "cash": {
            "title": "Payment methods",
            "unit": "UGX",
            "series": [
                {"label": "Cash received", **grouped_series(payments_qs, "payment_date", "amount", selected_group_by, start_date, end_date)},
            ],
            "pie": payment_breakdown,
        },
    }

    insights = []
    if total_profit < 0:
        insights.append({
            "level": "danger",
            "title": "Loss risk",
            "text": "Expenses are higher than revenue in this period. Review feed, labour, veterinary costs, and selling prices before adding more birds.",
        })
    elif profit_margin < 15 and total_revenue > 0:
        insights.append({
            "level": "warning",
            "title": "Thin margin",
            "text": "Profit margin is below 15%. Consider checking tray prices, discounting, wastage, and high-cost expense categories.",
        })
    else:
        insights.append({
            "level": "success",
            "title": "Margin is healthy",
            "text": "The farm is currently profitable for the selected period. Keep watching cash collection and production consistency.",
        })

    if outstanding_balance > total_revenue * Decimal("0.25") and total_revenue > 0:
        insights.append({
            "level": "warning",
            "title": "Receivables need attention",
            "text": "Outstanding customer balances are high compared with sales. Tighten credit terms or prioritize collections.",
        })
    if expense_ratio > 70:
        insights.append({
            "level": "warning",
            "title": "High cost base",
            "text": "Expenses are consuming more than 70% of revenue. Inspect the largest expense categories before new investment.",
        })
    if mortality_rate > 3:
        insights.append({
            "level": "danger",
            "title": "Mortality above target",
            "text": "Mortality is above 3% for the selected period. Check disease reports, house conditions, feed quality, and vaccination follow-up.",
        })
    if current_birds and egg_yield < Decimal("0.55"):
        insights.append({
            "level": "warning",
            "title": "Egg yield is low",
            "text": "Eggs per live bird are below a strong laying target. Review bird age, feed ration, light, disease pressure, and rejected eggs.",
        })
    if feed_per_egg > Decimal("0.18"):
        insights.append({
            "level": "warning",
            "title": "Feed efficiency watch",
            "text": "Feed used per egg looks high. Compare feed allocation with actual egg output and investigate wastage.",
        })

    recent_invoices = sales_qs.select_related("customer", "created_by").order_by("-created_at")[:5]
    recent_expenses = expenses_qs.select_related("category", "created_by").order_by("-expense_date", "-created_at")[:5]
    recent_eggs = eggs_qs.select_related("batch__house", "collected_by").order_by("-collection_date", "-collected_at")[:5]

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "selected_report_type": selected_report_type,
        "selected_group_by": selected_group_by,
        "selected_chart_type": selected_chart_type,
        "metrics": {
            "total_revenue": total_revenue,
            "cash_received": total_cash_received,
            "total_expenses": total_expenses,
            "profit": total_profit,
            "profit_margin": profit_margin,
            "collection_rate": collection_rate,
            "outstanding_balance": outstanding_balance,
            "total_eggs": total_eggs,
            "rejected_eggs": total_rejected_eggs,
            "total_deaths": total_deaths,
            "mortality_rate": mortality_rate,
            "feed_used_kg": total_feed_kg,
            "feed_per_egg": feed_per_egg,
            "current_birds": current_birds,
            "active_batches": active_batches.count(),
            "egg_yield": egg_yield,
        },
        "chart_data": chart_data,
        "expense_breakdown": expense_breakdown,
        "sales_breakdown": sales_breakdown,
        "insights": insights[:6],
        "recent_invoices": recent_invoices,
        "recent_expenses": recent_expenses,
        "recent_eggs": recent_eggs,
    }
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
                    defaults={"name": "Bird Batch Purchase", "is_active": True},
                )
                ExpenseTransaction.objects.create(
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

            messages.success(request, f"Batch {batch.batch_code} created successfully!")
            return redirect("birds")
    else:
        form = PoultryBatchForm()

    return render(request, "addbatch.html", {"form": form})
