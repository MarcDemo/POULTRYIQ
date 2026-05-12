from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Count, DecimalField, Max, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils.timezone import now
from datetime import date
from datetime import datetime
from decimal import Decimal, InvalidOperation

from accounts.decorators import worker_required, supervisor_required
from accounts.models import User
from alerts.models import Alert
from finance.models import ExpenseTransaction, SalaryPayment
from health.models import SicknessReport, TreatmentPlanItem, VaccinationSchedule
from sales.views import _build_product_stock
from inventory.models import InventoryTransaction, ReorderRule
from sales.models import ReceivableLedger, SaleInvoice, SaleItem
from .models import (
    PoultryBatch,
    DailyProduction,
    PoultryHouse,
    egg_collection,
    FeedRecord,
    CleaningRecord,
    CleaningPhoto,
    MortalityRecord,
    ApprovalStatus,
)
from .forms import PoultryBatchForm


def _batch_cycle_stage(batch):
    age_days = batch.current_age_days
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

    return {
        "label": stages[active_index]["label"],
        "color": stages[active_index]["color"],
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
def dashboard(request):
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
    search = request.GET.get("search")

    if status:
        batches = batches.filter(status=status)

    if house:
        batches = batches.filter(house__pk=house)

    if search:
        batches = batches.filter(batch_code__icontains=search)

    batches = batches.order_by("-created_at")

    houses = PoultryHouse.objects.filter(is_active=True)

    batch_data = []

    for batch in batches:
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
            "cycle": _batch_cycle_stage(batch),
            "assigned_workers": assigned_workers,
            "assigned_supervisors": assigned_supervisors,
        })

    context = {
        "batch_data": batch_data,
        "houses": PoultryHouse.objects.filter(is_active=True),
        "selected_status": status,
        "selected_house": house,
        "search_query": search,
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


@worker_required
def record_feed(request):
    batches = _get_worker_active_batches(request.user)

    if request.method == "POST":
        batch_id = request.POST.get("batch", "").strip()
        record_date_raw = request.POST.get("record_date", "").strip()
        feed_type = request.POST.get("feed_type", FeedRecord.FeedType.OTHER).strip()
        quantity_raw = request.POST.get("quantity", "").strip()
        time_given_raw = request.POST.get("time_given", "").strip()
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

        if feed_type not in dict(FeedRecord.FeedType.choices):
            errors.append("Please select a valid feed type.")

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
                record_date=record_date,
                feed_type=feed_type,
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
    ).select_related("batch__house")[:10]

    return render(
        request,
        "record_feed.html",
        {
            "batches": batches,
            "today": date.today(),
            "recent_feed_records": recent_feed_records,
            "feed_types": FeedRecord.FeedType.choices,
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
        notes = request.POST.get("notes", "").strip()

        errors = []
        selected_batch = batches.filter(pk=batch_id).first() if batch_id else None

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

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            egg_collection.objects.create(
                batch=selected_batch,
                collection_date=collection_date,
                eggs_collected=eggs_collected,
                eggs_rejected=eggs_rejected,
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
    ).select_related("batch__house", "recorded_by", "sickness_report").order_by("-created_at")

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

    context = {
        "tab": tab,
        "assigned_houses": assigned_houses,
        "assigned_houses_count": assigned_houses.count(),
        "pending_eggs": pending_eggs,
        "pending_feed": pending_feed,
        "pending_cleaning": pending_cleaning,
        "pending_mortality": pending_mortality,
        "pending_eggs_count": pending_eggs.count(),
        "pending_feed_count": pending_feed.count(),
        "pending_cleaning_count": pending_cleaning.count(),
        "pending_mortality_count": pending_mortality.count(),
    }
    return render(request, 'supapproval.html', context)


def login(request):
    return render(request, 'login.html')

def eggrec(request):
    return render(request, 'eggrec.html')

def feedrec(request):
    return render(request, 'feed_rec.html')


def investor(request):
    return render(request, 'investor.html')

def add_batch(request):
    if request.method == "POST":
        form = PoultryBatchForm(request.POST)
        if form.is_valid():
            batch = form.save(commit=False)
            batch.created_by = request.user
            batch.save()

            messages.success(request, f"Batch {batch.batch_code} created successfully!")
            return redirect("birds")
    else:
        form = PoultryBatchForm()

    return render(request, "addbatch.html", {"form": form})
