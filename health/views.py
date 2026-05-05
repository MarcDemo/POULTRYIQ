from datetime import date
from datetime import datetime

from django.contrib.auth import get_user_model
from django.contrib import messages
from django.db.models import Count, Max, Q, Sum
from django.shortcuts import redirect, render
from django.utils import timezone

from accounts.decorators import supervisor_required, worker_required
from alerts.models import Alert, AlertType
from poultry.models import (
    MortalityCause,
    MortalityRecord,
    PoultryBatch,
    PoultryHouse,
    egg_collection,
    FeedRecord,
    CleaningRecord,
)
from decimal import Decimal, InvalidOperation
from .models import SickbayCleaningRecord, SicknessReport, TreatmentPlanItem

User = get_user_model()

# Create your views here.
@worker_required
def mortality(request):
    worker_batches = PoultryBatch.objects.filter(
        house__in=request.user.houses.all(),
        status=PoultryBatch.Status.ACTIVE,
    ).select_related("house").order_by("house__house_code", "batch_code")
    causes = MortalityCause.objects.filter(is_active=True).order_by("name")

    if request.method == "POST":
        batch_id = request.POST.get("batch", "").strip()
        record_date_raw = request.POST.get("record_date", "").strip()
        count_raw = request.POST.get("count", "").strip()
        cause_id = request.POST.get("cause", "").strip()
        notes = request.POST.get("notes", "").strip()
        photos = request.FILES.getlist("photos")

        errors = []
        selected_batch = worker_batches.filter(pk=batch_id).first() if batch_id else None

        if not selected_batch:
            errors.append("Please select a valid batch from your assigned houses.")

        try:
            record_date = date.fromisoformat(record_date_raw)
        except ValueError:
            record_date = None
            errors.append("Please provide a valid date.")

        #proof of death photos should be mandatory all the time
        if not photos:
            errors.append("Please upload at least one photo as proof of death.")


        # enforce only today's mortality records
        if record_date and record_date != date.today():
            errors.append("Mortality records can only be created for today.")

        try:
            number_dead = int(count_raw)
            if number_dead < 1:
                errors.append("Number of deaths must be at least 1.")
        except ValueError:
            number_dead = None
            errors.append("Please provide a valid number of deaths.")

        selected_cause = None
        if cause_id:
            selected_cause = causes.filter(pk=cause_id).first()
            if not selected_cause:
                errors.append("Please select a valid mortality cause.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            MortalityRecord.objects.create(
                batch=selected_batch,
                record_date=record_date,
                number_dead=number_dead,
                cause=selected_cause,
                notes=notes,
                reported_by=request.user,
            )
            messages.success(request, "Mortality record saved successfully.")
            return redirect("mortality")

    recent_records = MortalityRecord.objects.filter(
        reported_by=request.user,
        batch__in=worker_batches,
        record_date=date.today(),
    ).select_related("batch__house", "cause")[:10]

    return render(
        request,
        "mortality.html",
        {
            "batches": worker_batches,
            "causes": causes,
            "today": date.today(),
            "recent_records": recent_records,
        },
    )

def _scoped_health_houses(user):
    if _role_code(user) == "SUPERVISOR":
        return user.houses.all().order_by("house_code", "name")
    return PoultryHouse.objects.filter(is_active=True).order_by("house_code", "name")


def _scoped_sickness_cases(user):
    return _scope_sickness_reports_for_user(
        SicknessReport.objects.select_related("house_ref", "batch__house", "reported_by").prefetch_related(
            "treatment_items",
            "treatment_items__alert",
        ),
        user,
    )


def _scoped_treatment_items(user):
    queryset = TreatmentPlanItem.objects.select_related(
        "sickness_report__house_ref",
        "sickness_report__batch",
        "sickness_report__reported_by",
        "alert",
        "marked_given_by",
    )
    if _role_code(user) == "SUPERVISOR":
        queryset = queryset.filter(sickness_report__house_ref__in=user.houses.all())
    return queryset


def _get_health_alert_type():
    alert_type, _ = AlertType.objects.get_or_create(
        code=AlertType.AlertTypeCode.HEALTH,
        defaults={
            "name": "Health Alert",
            "description": "Vet diagnosis and treatment reminders.",
        },
    )
    return alert_type


def _get_treatment_alert_receiver(report, fallback_user):
    reporter_role = _role_code(report.reported_by)
    if report.reported_by.is_active and reporter_role in {"SUPERVISOR", "MANAGER", "OWNER"}:
        return report.reported_by

    supervisors = User.objects.filter(
        is_active=True,
        role__code__in=["SUPERVISOR", "MANAGER", "OWNER"],
    )
    if report.house_ref_id:
        supervisors = supervisors.filter(houses=report.house_ref)

    return supervisors.distinct().first() or fallback_user


def _create_treatment_alert(plan_item, receiver):
    due_date = None
    if plan_item.scheduled_for:
        due_date = timezone.make_aware(datetime.combine(plan_item.scheduled_for, datetime.min.time()))

    return Alert.objects.create(
        alert_type=_get_health_alert_type(),
        title=f"Treatment due for {plan_item.sickness_report.case_label}",
        message=(
            f"Diagnosis: {plan_item.sickness_report.diagnosis_label}\n"
            f"Case size: {plan_item.sickness_report.affected} bird(s)\n"
            f"Medicine: {plan_item.medicine_name}\n"
            f"Dosage: {plan_item.dosage}\n"
            f"Instructions: {plan_item.instructions or 'Follow the vet guidance recorded in the treatment plan.'}"
        ),
        receiver=receiver,
        sender=None,
        priority=Alert.Priority.HIGH,
        related_house=plan_item.sickness_report.house_ref,
        related_batch=plan_item.sickness_report.batch,
        due_date=due_date,
        persist_until_resolved=True,
    )


@supervisor_required
def treatment(request):
    role_code = _role_code(request.user)
    sickness_cases = _scoped_sickness_cases(request.user)
    open_cases = sickness_cases.exclude(case_status=SicknessReport.CaseStatus.TREATMENT_COMPLETED)
    treatment_items = _scoped_treatment_items(request.user).order_by("is_given", "scheduled_for", "-created_at")
    pending_treatment_items = treatment_items.filter(is_given=False)
    completed_treatment_items = treatment_items.filter(is_given=True)[:10]

    if request.method == "POST":
        form_action = request.POST.get("form_action", "").strip()

        if form_action == "record_treatment_plan":
            sickness_report_id = request.POST.get("sickness_report", "").strip()
            diagnosis = request.POST.get("diagnosis", "").strip()
            vet_name = request.POST.get("vet_name", "").strip()
            vet_visit_date_raw = request.POST.get("vet_visit_date", "").strip()
            medicine_name = request.POST.get("medicine_name", "").strip()
            dosage = request.POST.get("dosage", "").strip()
            administration_route = request.POST.get("administration_route", "").strip()
            instructions = request.POST.get("instructions", "").strip()
            scheduled_for_raw = request.POST.get("scheduled_for", "").strip()
            diagnosis_notes = request.POST.get("diagnosis_notes", "").strip()

            errors = []
            selected_report = open_cases.filter(pk=sickness_report_id).first() if sickness_report_id else None

            if not selected_report:
                errors.append("Please select a valid sickness case.")
            if not diagnosis:
                errors.append("Please record the vet diagnosis.")
            if not vet_name:
                errors.append("Please enter the vet's name.")
            if not medicine_name:
                errors.append("Please record the medicine prescribed.")
            if not dosage:
                errors.append("Please record the dosage.")

            try:
                vet_visit_date = date.fromisoformat(vet_visit_date_raw)
            except ValueError:
                vet_visit_date = None
                errors.append("Please provide a valid vet visit date.")

            scheduled_for = None
            if scheduled_for_raw:
                try:
                    scheduled_for = date.fromisoformat(scheduled_for_raw)
                except ValueError:
                    errors.append("Please provide a valid treatment schedule date.")

            

            if errors:
                for error in errors:
                    messages.error(request, error)
            else:
                selected_report.disease = diagnosis
                selected_report.vet_name = vet_name
                selected_report.vet_visit_date = vet_visit_date
                selected_report.diagnosis_notes = diagnosis_notes
                selected_report.case_status = SicknessReport.CaseStatus.DIAGNOSED
                selected_report.treatment_completed_at = None
                selected_report.save(
                    update_fields=[
                        "disease",
                        "vet_name",
                        "vet_visit_date",
                        "diagnosis_notes",
                        "case_status",
                        "treatment_completed_at",
                    ]
                )

                plan_item = TreatmentPlanItem.objects.create(
                    sickness_report=selected_report,
                    medicine_name=medicine_name,
                    dosage=dosage,
                    administration_route=administration_route,
                    instructions=instructions,
                    scheduled_for=scheduled_for or vet_visit_date,
                    
                    created_by=request.user,
                )

                receiver = _get_treatment_alert_receiver(selected_report, request.user)
                plan_item.alert = _create_treatment_alert(plan_item, receiver)
                plan_item.save(update_fields=["alert"]) 

                messages.success(request, "Vet diagnosis and treatment plan saved.")
                return redirect("treatment")

        elif form_action == "mark_given":
            plan_id = request.POST.get("plan_id", "").strip()
            selected_plan = pending_treatment_items.filter(pk=plan_id).first() if plan_id else None

            if not selected_plan:
                messages.error(request, "Please choose a valid treatment item to mark as given.")
            else:
                selected_plan.is_given = True
                selected_plan.given_at = timezone.now()
                selected_plan.marked_given_by = request.user
                selected_plan.save(update_fields=["is_given", "given_at", "marked_given_by"])

                if selected_plan.alert_id:
                    selected_plan.alert.mark_as_resolved()

                parent_case = selected_plan.sickness_report
                if not parent_case.treatment_items.filter(is_given=False).exists():
                    parent_case.case_status = SicknessReport.CaseStatus.TREATMENT_COMPLETED
                    parent_case.treatment_completed_at = timezone.now()
                else:
                    parent_case.case_status = SicknessReport.CaseStatus.DIAGNOSED
                    parent_case.treatment_completed_at = None
                parent_case.save(update_fields=["case_status", "treatment_completed_at"])

                messages.success(request, "Treatment dose marked as given.")
                return redirect("treatment")

        else:
            messages.error(request, "Unknown treatment action.")

    return render(
        request,
        "treatment.html",
        {
            "base_template": "supbase.html" if role_code == "SUPERVISOR" else "base.html",
            "today": date.today(),
            "open_cases": open_cases.order_by("-date", "-created_at")[:20],
            "pending_vet_cases_count": open_cases.filter(case_status=SicknessReport.CaseStatus.REPORTED).count(),
            "pending_treatment_items": pending_treatment_items,
            "completed_treatment_items": completed_treatment_items,
            "pending_treatment_count": pending_treatment_items.count(),
            "active_treatment_alerts": pending_treatment_items.filter(
                alert__status__in=[Alert.Status.UNREAD, Alert.Status.READ, Alert.Status.ACKNOWLEDGED]
            ).count(),
        },
    )

def vaccination(request):
    return render(request, 'vaccination.html')

def vaccine_report(request):
    vaccinations = [
        {'date': '5th april', 'house': 'house B', 'vaccine': 'newcastle vaccine', 'status': 'scheduled'},
        {'date': '4th april', 'house': 'house A', 'vaccine': 'gumboro vaccine', 'status': 'done'},
    ]
    return render(request, 'vaccine_report.html', {'vaccinations': vaccinations})


def _role_code(user) -> str:
    return (getattr(user.role, "code", "") or "").upper()


def _scope_sickness_reports_for_user(queryset, user):
    if _role_code(user) == "SUPERVISOR":
        return queryset.filter(house_ref__in=user.houses.all())
    return queryset


@supervisor_required
def report_sickness(request):
    houses = _scoped_health_houses(request.user)

    if request.method == "POST":
        house_id = request.POST.get("house", "").strip()
        affected_raw = request.POST.get("affected", "").strip()
        symptoms = request.POST.get("symptoms", "").strip()
        action = request.POST.get("action", "").strip()
        notes = request.POST.get("notes", "").strip()
        report_date_raw = request.POST.get("date", "").strip()
       

        errors = []
        selected_house = houses.filter(pk=house_id).first() if house_id else None
        selected_batch = None

        if not selected_house:
            errors.append("Please select a valid house from your assignment.")
        else:
            selected_batch = (
                PoultryBatch.objects.filter(
                    house=selected_house,
                    status=PoultryBatch.Status.ACTIVE,
                )
                .select_related("house")
                .order_by("-created_at")
                .first()
            )

        try:
            report_date = date.fromisoformat(report_date_raw)
        except ValueError:
            report_date = None
            errors.append("Please provide a valid sickness report date.")

        try:
            affected = int(affected_raw)
            if affected < 1:
                errors.append("Number of sick birds must be at least 1.")
        except ValueError:
            affected = None
            errors.append("Please provide a valid number of sick birds.")

        if not symptoms:
            errors.append("Please describe the sickness signs or symptoms.")

        if action not in dict(SicknessReport.ACTION_CHOICES):
            errors.append("Please select a valid action taken.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            SicknessReport.objects.create(
                date=report_date,
                house=selected_house.name or selected_house.house_code,
                house_ref=selected_house,
                batch=selected_batch,
                symptoms=symptoms,
                disease="",
                affected=affected,
                action=action,
                notes=notes,
                image=request.FILES.get("image"),
                reported_by=request.user,
                case_status=SicknessReport.CaseStatus.REPORTED,
            )
            messages.success(request, "Sickness case saved successfully.")
            return redirect("report_sickness")

    return render(
        request,
        "report_sickness.html",
        {
            "base_template": "supbase.html" if _role_code(request.user) == "SUPERVISOR" else "base.html",
            "houses": houses,
            "action_choices": SicknessReport.ACTION_CHOICES,
            "recent_cases": _scoped_sickness_cases(request.user).order_by("-date", "-created_at")[:8],
            "today": date.today(),
        },
    )


@worker_required
def record_sickbay_cleaning(request):
    assigned_houses = request.user.houses.all().order_by("house_code", "name")
    sickbay_reports = (
        SicknessReport.objects.filter(
            action="sickbay",
            house_ref__in=assigned_houses,
        )
        .select_related("house_ref", "batch", "reported_by")
        .order_by("-date", "-created_at")
    )

    if request.method == "POST":
        report_id = request.POST.get("sickness_report", "").strip()
        record_date_raw = request.POST.get("record_date", "").strip()
        sickbay_cleaned = bool(request.POST.get("sickbay_cleaned"))
        disinfection_done = bool(request.POST.get("disinfected"))
        equipment_cleaned = bool(request.POST.get("equipment_cleaned"))
        water_changed = bool(request.POST.get("water_changed"))
        notes = request.POST.get("notes", "").strip()

        errors = []
        selected_report = sickbay_reports.filter(pk=report_id).first() if report_id else None

        if not selected_report:
            errors.append("Please select a valid sickbay case.")

        try:
            record_date = date.fromisoformat(record_date_raw)
        except ValueError:
            record_date = None
            errors.append("Please provide a valid sickbay cleaning date.")

        # enforce only today's sickbay cleaning records
        if record_date and record_date != date.today():
            errors.append("Sickbay cleaning records can only be created for today.")

        if not any([sickbay_cleaned, disinfection_done, equipment_cleaned, water_changed]):
            errors.append("Please tick at least one sickbay cleaning task.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            SickbayCleaningRecord.objects.create(
                sickness_report=selected_report,
                record_date=record_date,
                sickbay_cleaned=sickbay_cleaned,
                disinfection_done=disinfection_done,
                equipment_cleaned=equipment_cleaned,
                water_changed=water_changed,
                notes=notes,
                recorded_by=request.user,
            )
            messages.success(request, "Sickbay cleaning record saved successfully.")
            return redirect("record_sickbay_cleaning")

    recent_cleaning_records = (
        SickbayCleaningRecord.objects.filter(
            recorded_by=request.user,
            sickness_report__house_ref__in=assigned_houses,
            record_date=date.today(),
        )
        .select_related("sickness_report__house_ref")
        .order_by("-record_date", "-created_at")[:7]
    )

    return render(
        request,
        "record_sickbay_cleaning.html",
        {
            "sickbay_reports": sickbay_reports,
            "recent_cleaning_records": recent_cleaning_records,
            "today": date.today(),
        },
    )


def view_sickness_reports(request):
    if not request.user.is_authenticated:
        return redirect("login")

    role_code = _role_code(request.user)
    if role_code == "WORKER":
        messages.error(request, "Workers can only submit sickness reports from the worker panel.")
        return redirect("workersdash")

    reports = _scope_sickness_reports_for_user(
        SicknessReport.objects.select_related("house_ref", "batch__house", "reported_by").prefetch_related(
            "treatment_items",
        ),
        request.user,
    )

    start_date = request.GET.get("start", "").strip()
    end_date = request.GET.get("end", "").strip()
    house_id = request.GET.get("house", "").strip()
    disease = request.GET.get("disease", "").strip()
    action = request.GET.get("action", "").strip()
    isolated = request.GET.get("isolated", "").strip()

    if start_date:
        reports = reports.filter(date__gte=start_date)
    if end_date:
        reports = reports.filter(date__lte=end_date)
    if house_id:
        reports = reports.filter(house_ref_id=house_id)
    if disease:
        reports = reports.filter(
            Q(disease__icontains=disease)
            | Q(symptoms__icontains=disease)
            | Q(bird_identifier__icontains=disease)
        )
    if action in dict(SicknessReport.ACTION_CHOICES):
        reports = reports.filter(action=action)
    if isolated == "yes":
        reports = reports.filter(isolated=True)
    elif isolated == "no":
        reports = reports.filter(isolated=False)

    reports = reports.annotate(
        cleaning_sessions=Count("sickbay_cleanings", distinct=True),
        last_cleaned_at=Max("sickbay_cleanings__record_date"),
        treatment_items_count=Count("treatment_items", distinct=True),
        pending_treatment_count=Count(
            "treatment_items",
            filter=Q(treatment_items__is_given=False),
            distinct=True,
        ),
    ).order_by("-date", "-created_at")

    total_cases = reports.count()
    total_affected = reports.aggregate(total=Sum("affected"))["total"] or 0
    isolated_cases = reports.filter(isolated=True).count()
    common_disease_entry = (
        reports.exclude(disease="")
        .values("disease")
        .annotate(total=Count("id"))
        .order_by("-total", "disease")
        .first()
    )
    common_disease = common_disease_entry["disease"] if common_disease_entry else "N/A"

    house_choices = _scoped_health_houses(request.user)

    return render(
        request,
        "view_sickness.html",
        {
            "base_template": "supbase.html" if role_code == "SUPERVISOR" else "base.html",
            "reports": reports,
            "house_choices": house_choices,
            "total_cases": total_cases,
            "total_affected": total_affected,
            "isolated_cases": isolated_cases,
            "common_disease": common_disease,
            "selected_start": start_date,
            "selected_end": end_date,
            "selected_house": house_id,
            "selected_disease": disease,
            "selected_action": action,
            "selected_isolated": isolated,
            "action_choices": SicknessReport.ACTION_CHOICES,
        },
    )

@worker_required
def sickbay(request):

    return render(request, 'sickbay.html')


@worker_required
def sick_record_egg(request):
    batches = PoultryBatch.objects.filter(
        house__in=request.user.houses.all(),
        status=PoultryBatch.Status.ACTIVE,
    ).select_related("house").order_by("house__house_code", "batch_code")

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

        # only allow today's entries
        if collection_date and collection_date != date.today():
            errors.append("Egg collection records for sickbay must be for today.")

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
            messages.success(request, "Sickbay: Egg collection saved.")
            return redirect("sick_record_egg")

    recent_egg_records = egg_collection.objects.filter(
        collected_by=request.user,
        batch__in=batches,
        collection_date=date.today(),
    ).select_related("batch__house")[:10]

    return render(request, "record_egg_sickbay.html", {"batches": batches, "today": date.today(), "recent_egg_records": recent_egg_records})


@worker_required
def sick_record_feed(request):
    batches = PoultryBatch.objects.filter(
        house__in=request.user.houses.all(),
        status=PoultryBatch.Status.ACTIVE,
    ).select_related("house").order_by("house__house_code", "batch_code")

    if request.method == "POST":
        batch_id = request.POST.get("batch", "").strip()
        record_date_raw = request.POST.get("record_date", "").strip()
        feed_type = request.POST.get("feed_type", FeedRecord.FeedType.OTHER).strip()
        quantity_raw = request.POST.get("quantity", "").strip()
        time_given_raw = request.POST.get("time_given", "").strip()
        notes = request.POST.get("notes", "").strip()

        errors = []
        selected_batch = batches.filter(pk=batch_id).first() if batch_id else None

        if not selected_batch:
            errors.append("Please select a valid batch from your assigned houses.")

        try:
            record_date = date.fromisoformat(record_date_raw)
        except ValueError:
            record_date = None
            errors.append("Please provide a valid date.")

        if record_date and record_date != date.today():
            errors.append("Feed records for sickbay must be for today.")

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
            messages.success(request, "Sickbay: Feed record saved.")
            return redirect("sick_record_feed")

    recent_feed_records = FeedRecord.objects.filter(
        recorded_by=request.user,
        batch__in=batches,
        record_date=date.today(),
    ).select_related("batch__house")[:10]

    return render(request, "record_feed_sickbay.html", {"batches": batches, "today": date.today(), "recent_feed_records": recent_feed_records, "feed_types": FeedRecord.FeedType.choices})


@worker_required
def sick_record_cleaning(request):
    batches = PoultryBatch.objects.filter(
        house__in=request.user.houses.all(),
        status=PoultryBatch.Status.ACTIVE,
    ).select_related("house").order_by("house__house_code", "batch_code")

    if request.method == "POST":
        batch_id = request.POST.get("batch", "").strip()
        record_date_raw = request.POST.get("record_date", "").strip()
        house_cleaned = bool(request.POST.get("cleaned"))
        disinfection_done = bool(request.POST.get("disinfected"))
        water_changed = bool(request.POST.get("water_changed"))
        notes = request.POST.get("notes", "").strip()

        errors = []
        selected_batch = batches.filter(pk=batch_id).first() if batch_id else None

        if not selected_batch:
            errors.append("Please select a valid batch from your assigned houses.")

        try:
            record_date = date.fromisoformat(record_date_raw)
        except ValueError:
            record_date = None
            errors.append("Please provide a valid date.")

        if record_date and record_date != date.today():
            errors.append("Cleaning records for sickbay must be for today.")

        if not any([house_cleaned, disinfection_done, water_changed]):
            errors.append("Please tick at least one cleaning task.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            CleaningRecord.objects.create(
                batch=selected_batch,
                record_date=record_date,
                house_cleaned=house_cleaned,
                disinfection_done=disinfection_done,
                water_changed=water_changed,
                notes=notes,
                recorded_by=request.user,
            )
            messages.success(request, "Sickbay: Cleaning record saved.")
            return redirect("sick_record_cleaning")

    recent_cleaning_records = CleaningRecord.objects.filter(
        recorded_by=request.user,
        batch__in=batches,
        record_date=date.today(),
    ).select_related("batch__house")[:7]

    return render(request, "record_cleaning_sickbay.html", {"batches": batches, "today": date.today(), "recent_cleaning_records": recent_cleaning_records})


@worker_required
def sick_mortality(request):
    batches = PoultryBatch.objects.filter(
        house__in=request.user.houses.all(),
        status=PoultryBatch.Status.ACTIVE,
    ).select_related("house").order_by("house__house_code", "batch_code")
    causes = MortalityCause.objects.filter(is_active=True).order_by("name")

    if request.method == "POST":
        batch_id = request.POST.get("batch", "").strip()
        record_date_raw = request.POST.get("record_date", "").strip()
        count_raw = request.POST.get("count", "").strip()
        cause_id = request.POST.get("cause", "").strip()
        notes = request.POST.get("notes", "").strip()

        errors = []
        selected_batch = batches.filter(pk=batch_id).first() if batch_id else None

        if not selected_batch:
            errors.append("Please select a valid batch from your assigned houses.")

        try:
            record_date = date.fromisoformat(record_date_raw)
        except ValueError:
            record_date = None
            errors.append("Please provide a valid date.")

        if record_date and record_date != date.today():
            errors.append("Mortality records for sickbay must be for today.")

        try:
            number_dead = int(count_raw)
            if number_dead < 1:
                errors.append("Number of deaths must be at least 1.")
        except ValueError:
            number_dead = None
            errors.append("Please provide a valid number of deaths.")

        selected_cause = None
        if cause_id:
            selected_cause = causes.filter(pk=cause_id).first()
            if not selected_cause:
                errors.append("Please select a valid mortality cause.")

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            MortalityRecord.objects.create(
                batch=selected_batch,
                record_date=record_date,
                number_dead=number_dead,
                cause=selected_cause,
                notes=notes,
                reported_by=request.user,
            )
            messages.success(request, "Sickbay: Mortality record saved.")
            return redirect("sick_mortality")

    recent_records = MortalityRecord.objects.filter(
        reported_by=request.user,
        batch__in=batches,
        record_date=date.today(),
    ).select_related("batch__house", "cause")[:10]

    return render(request, "mortality_sickbay.html", {"batches": batches, "causes": causes, "today": date.today(), "recent_records": recent_records})
