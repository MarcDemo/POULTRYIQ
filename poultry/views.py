from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Count, Max, Sum
from django.utils.timezone import now
from datetime import date
from datetime import datetime
from decimal import Decimal, InvalidOperation

from accounts.decorators import worker_required, supervisor_required
from health.models import SicknessReport, TreatmentPlanItem
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

# Create your views here.
def dashboard(request):
    return render(request, 'dashboard.html')

def birds(request):
    batches = PoultryBatch.objects.select_related("house")

    # 🔍 filters
    status = request.GET.get("status")
    house = request.GET.get("house")
    search = request.GET.get("search")

    if status:
        batches = batches.filter(status=status)

    if house:
        batches = batches.filter(house__id=house)

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

        current_birds = batch.initial_quantity - total_mortality

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
        "pending_eggs": pending_eggs,
        "pending_feed": pending_feed,
        "pending_cleaning": pending_cleaning,
        "pending_mortality": pending_mortality,
        "total_pending": total_pending,
        "today_eggs": today_eggs,
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
