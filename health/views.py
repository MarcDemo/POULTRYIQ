from datetime import date
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.contrib import messages
from django.db.models import Count, Max, Q, Sum
from django.shortcuts import redirect, render
from django.utils import timezone

from accounts.decorators import supervisor_required, worker_required
from alerts.models import Alert, AlertType
from .services import get_health_alert_type, get_treatment_alert_receiver, create_treatment_alert
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


# Using alert helper functions from health.services


@supervisor_required
def treatment(request):
    role_code = _role_code(request.user)
    sickness_cases = _scoped_sickness_cases(request.user)
    open_cases = sickness_cases.exclude(case_status=SicknessReport.CaseStatus.TREATMENT_COMPLETED)
    treatment_items = _scoped_treatment_items(request.user).order_by("is_given", "scheduled_for", "-created_at")
    pending_treatment_items = treatment_items.filter(is_given=False)
    completed_treatment_items = treatment_items.filter(is_given=True)[:10]
    # compute which pending items are due (scheduled datetime <= now)
    now = timezone.now()
    due_ids = []
    for it in pending_treatment_items:
        sf = it.scheduled_for
        if not sf:
            continue
        # ensure timezone-aware comparison
        try:
            if timezone.is_naive(sf):
                sf = timezone.make_aware(datetime.combine(sf.date(), sf.time()))
        except Exception:
            pass
        if sf <= now:
            due_ids.append(it.pk)

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
            scheduled_time_raw = request.POST.get("scheduled_time", "").strip()
            start_date_raw = request.POST.get("start_date", "").strip()
            start_time_raw = request.POST.get("start_time", "").strip()
            duration_days_raw = request.POST.get("duration_days", "").strip()
            frequency_hours_raw = request.POST.get("frequency_hours", "").strip()
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
                    if scheduled_time_raw:
                        scheduled_for = datetime.fromisoformat(f"{scheduled_for_raw}T{scheduled_time_raw}")
                    else:
                        scheduled_for = datetime.fromisoformat(f"{scheduled_for_raw}T00:00:00")
                except ValueError:
                    errors.append("Please provide a valid treatment schedule date/time.")

            start_datetime = None
            duration_days = None
            frequency_hours = None
            if start_date_raw:
                try:
                    if start_time_raw:
                        start_datetime = datetime.fromisoformat(f"{start_date_raw}T{start_time_raw}")
                    else:
                        start_datetime = datetime.fromisoformat(f"{start_date_raw}T00:00:00")
                except ValueError:
                    errors.append("Please provide a valid start date/time for the treatment plan.")

            if duration_days_raw:
                try:
                    duration_days = int(duration_days_raw)
                    if duration_days < 1:
                        errors.append("Duration must be at least 1 day.")
                except ValueError:
                    errors.append("Please provide a valid integer duration in days.")

            if frequency_hours_raw:
                try:
                    frequency_hours = int(frequency_hours_raw)
                    if frequency_hours < 1:
                        errors.append("Frequency must be at least 1 hour.")
                except ValueError:
                    errors.append("Please provide a valid integer frequency in hours.")

            

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

                # Build schedule of doses using datetime + hourly frequency
                scheduled_datetimes = []
                if start_datetime and duration_days and frequency_hours:
                    # schedule from start_datetime up to (start + duration_days)
                    end_datetime = start_datetime + timedelta(days=duration_days) - timedelta(seconds=1)
                    current = start_datetime
                    while current <= end_datetime:
                        scheduled_datetimes.append(current)
                        current = current + timedelta(hours=frequency_hours)
                elif scheduled_for:
                    scheduled_datetimes = [scheduled_for]
                else:
                    # fallback to vet visit date (at midnight) if present
                    if vet_visit_date:
                        scheduled_datetimes = [datetime.combine(vet_visit_date, datetime.min.time())]

                receiver = get_treatment_alert_receiver(selected_report, request.user)
                created_items = []
                for due_dt in scheduled_datetimes:
                    item = TreatmentPlanItem.objects.create(
                        sickness_report=selected_report,
                        medicine_name=medicine_name,
                        dosage=dosage,
                        administration_route=administration_route,
                        instructions=instructions,
                        scheduled_for=due_dt,
                        start_datetime=start_datetime,
                        duration_days=duration_days,
                        frequency_hours=frequency_hours,
                        created_by=request.user,
                    )
                    alert_obj = create_treatment_alert(item, receiver)
                    if alert_obj:
                        item.alert = alert_obj
                        item.save(update_fields=["alert"]) 
                    created_items.append(item)

                # update isolation name to housename-disease (as requested)
                try:
                    selected_report.isolation_name = f"{selected_report.house_label}-{selected_report.disease or diagnosis}"
                    selected_report.save(update_fields=["isolation_name"])
                except Exception:
                    # non-fatal
                    pass

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
            "due_ids": due_ids,
            "now": now,
        },
    )

def vaccination(request):
    role_code = _role_code(request.user)
    houses = _scoped_health_houses(request.user)

    if request.method == "POST":
        action = request.POST.get("form_action", "").strip()
        if action == "create_schedule":
            house_id = request.POST.get("house", "").strip()
            batch_id = request.POST.get("batch", "").strip()
            vaccine_name = request.POST.get("vaccine_name", "").strip()
            brand = request.POST.get("brand", "").strip()
            dosage = request.POST.get("dosage", "").strip()
            administration_mode = request.POST.get("administration_mode", "").strip()
            scheduled_date_raw = request.POST.get("scheduled_date", "").strip()
            scheduled_time_raw = request.POST.get("scheduled_time", "").strip()
            frequency_days_raw = request.POST.get("frequency_days", "").strip()
            vet_name = request.POST.get("vet_name", "").strip()
            brand_expiry_raw = request.POST.get("brand_expiry_date", "").strip()

            errors = []
            selected_house = houses.filter(pk=house_id).first() if house_id else None
            selected_batch = None
            if batch_id:
                from poultry.models import PoultryBatch

                selected_batch = PoultryBatch.objects.filter(pk=batch_id, house__in=houses).first()

            if not selected_house:
                errors.append("Please select a valid house from your assignment.")
            if not vaccine_name:
                errors.append("Please provide the vaccine name.")

            from datetime import datetime
            scheduled_for = None
            if scheduled_date_raw:
                try:
                    if scheduled_time_raw:
                        scheduled_for = datetime.fromisoformat(f"{scheduled_date_raw}T{scheduled_time_raw}")
                    else:
                        scheduled_for = datetime.fromisoformat(f"{scheduled_date_raw}T00:00:00")
                except ValueError:
                    errors.append("Please provide a valid scheduled date/time.")

            frequency_days = None
            if frequency_days_raw:
                try:
                    frequency_days = int(frequency_days_raw)
                    if frequency_days < 1:
                        errors.append("Frequency must be at least 1 day.")
                except ValueError:
                    errors.append("Please provide a valid integer frequency in days.")

            brand_expiry = None
            if brand_expiry_raw:
                try:
                    brand_expiry = datetime.fromisoformat(brand_expiry_raw).date()
                except ValueError:
                    errors.append("Please provide a valid brand expiry date.")

            if errors:
                for e in errors:
                    messages.error(request, e)
            else:
                from health.models import VaccinationSchedule
                schedule = VaccinationSchedule.objects.create(
                    house_ref=selected_house,
                    batch=selected_batch,
                    vaccine_name=vaccine_name,
                    brand=brand,
                    dosage=dosage,
                    administration_mode=administration_mode,
                    frequency_days=frequency_days,
                    scheduled_for=scheduled_for,
                    vet_name=vet_name,
                    brand_expiry_date=brand_expiry,
                    created_by=request.user,
                )
                # create alert if within 2 days
                from .services import get_vaccination_alert_receiver, create_vaccination_alert

                receiver = get_vaccination_alert_receiver(schedule, request.user)
                alert_obj = create_vaccination_alert(schedule, receiver)
                if alert_obj:
                    schedule.alert = alert_obj
                    schedule.save(update_fields=["alert"])

                messages.success(request, "Vaccination schedule saved.")
                return redirect("vaccination")

        elif action == "mark_administered":
            sched_id = request.POST.get("schedule_id", "").strip()
            from health.models import VaccinationSchedule

            selected = VaccinationSchedule.objects.filter(pk=sched_id, status=VaccinationSchedule.Status.SCHEDULED).first() if sched_id else None
            if not selected:
                messages.error(request, "Please select a valid scheduled vaccination to mark as administered.")
            else:
                from django.utils import timezone
                now = timezone.now()
                if selected.scheduled_for and selected.scheduled_for > now:
                    messages.error(request, "Cannot mark administered before the scheduled time.")
                else:
                    selected.status = VaccinationSchedule.Status.ADMINISTERED
                    selected.administered_at = now
                    selected.administered_by = request.user
                    num_raw = request.POST.get("number_vaccinated", "").strip()
                    num = None
                    if num_raw:
                        try:
                            num = int(num_raw)
                            if num < 0:
                                raise ValueError()
                        except ValueError:
                            messages.error(request, "Please enter a valid non-negative integer for number vaccinated.")
                            return redirect("vaccination")
                    selected.number_vaccinated = num

                    selected.save(update_fields=["status", "administered_at", "administered_by", "number_vaccinated"])
                    if selected.alert_id:
                        selected.alert.mark_as_resolved()
                    messages.success(request, "Vaccination marked as administered.")
                return redirect("vaccination")

    # GET - show form and existing schedules for supervisor's houses
    from health.models import VaccinationSchedule
    schedules = VaccinationSchedule.objects.filter(house_ref__in=houses).select_related("house_ref", "batch", "created_by", "administered_by").order_by("-scheduled_for")
    return render(request, 'vaccination.html', {"houses": houses, "schedules": schedules, "base_template": "supbase.html" if role_code == "SUPERVISOR" else "base.html"})

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
    assigned_sick_reports = (
        SicknessReport.objects.filter(action="sickbay", house_ref__in=request.user.houses.all())
        .select_related("house_ref", "batch")
        .order_by("-date", "-created_at")
    )

    if request.method == "POST":
        batch_id = request.POST.get("batch", "").strip()
        sickness_report_id = request.POST.get("sickness_report", "").strip()
        collection_date_raw = request.POST.get("collection_date", "").strip()
        total_eggs_raw = request.POST.get("total_eggs", "").strip()
        broken_eggs_raw = request.POST.get("broken_eggs", "0").strip()
        notes = request.POST.get("notes", "").strip()

        errors = []
        selected_batch = batches.filter(pk=batch_id).first() if batch_id else None
        selected_report = assigned_sick_reports.filter(pk=sickness_report_id).first() if sickness_report_id else None
        if selected_report and selected_report.batch:
            selected_batch = selected_report.batch

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

        if not selected_batch and not selected_report:
            errors.append("Please select a valid batch or sickbay case from your assignment.")

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
                sickness_report=selected_report,
            )
            messages.success(request, "Sickbay: Egg collection saved.")
            return redirect("sick_record_egg")

    from django.db.models import Q
    recent_egg_records = egg_collection.objects.filter(
        collected_by=request.user,
        collection_date=date.today(),
    ).filter(Q(batch__in=batches) | Q(sickness_report__in=assigned_sick_reports)).select_related("batch__house")[:10]

    return render(request, "record_egg_sickbay.html", {"batches": batches, "today": date.today(), "recent_egg_records": recent_egg_records, "sick_reports": assigned_sick_reports})


@worker_required
def sick_record_feed(request):
    batches = PoultryBatch.objects.filter(
        house__in=request.user.houses.all(),
        status=PoultryBatch.Status.ACTIVE,
    ).select_related("house").order_by("house__house_code", "batch_code")
    assigned_sick_reports = (
        SicknessReport.objects.filter(action="sickbay", house_ref__in=request.user.houses.all())
        .select_related("house_ref", "batch")
        .order_by("-date", "-created_at")
    )

    if request.method == "POST":
        batch_id = request.POST.get("batch", "").strip()
        sickness_report_id = request.POST.get("sickness_report", "").strip()
        record_date_raw = request.POST.get("record_date", "").strip()
        feed_type = request.POST.get("feed_type", FeedRecord.FeedType.OTHER).strip()
        quantity_raw = request.POST.get("quantity", "").strip()
        time_given_raw = request.POST.get("time_given", "").strip()
        notes = request.POST.get("notes", "").strip()

        errors = []
        selected_batch = batches.filter(pk=batch_id).first() if batch_id else None
        selected_report = assigned_sick_reports.filter(pk=sickness_report_id).first() if sickness_report_id else None
        if selected_report and selected_report.batch:
            selected_batch = selected_report.batch

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

        if not selected_batch and not selected_report:
            errors.append("Please select a valid batch or sickbay case from your assignment.")

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
                sickness_report=selected_report,
            )
            messages.success(request, "Sickbay: Feed record saved.")
            return redirect("sick_record_feed")

    from django.db.models import Q
    recent_feed_records = FeedRecord.objects.filter(
        recorded_by=request.user,
        record_date=date.today(),
    ).filter(Q(batch__in=batches) | Q(sickness_report__in=assigned_sick_reports)).select_related("batch__house")[:10]

    return render(request, "record_feed_sickbay.html", {"batches": batches, "today": date.today(), "recent_feed_records": recent_feed_records, "feed_types": FeedRecord.FeedType.choices, "sick_reports": assigned_sick_reports})


@worker_required
def sick_record_cleaning(request):
    batches = PoultryBatch.objects.filter(
        house__in=request.user.houses.all(),
        status=PoultryBatch.Status.ACTIVE,
    ).select_related("house").order_by("house__house_code", "batch_code")
    assigned_sick_reports = (
        SicknessReport.objects.filter(action="sickbay", house_ref__in=request.user.houses.all())
        .select_related("house_ref", "batch")
        .order_by("-date", "-created_at")
    )

    if request.method == "POST":
        batch_id = request.POST.get("batch", "").strip()
        sickness_report_id = request.POST.get("sickness_report", "").strip()
        record_date_raw = request.POST.get("record_date", "").strip()
        house_cleaned = bool(request.POST.get("cleaned"))
        disinfection_done = bool(request.POST.get("disinfected"))
        water_changed = bool(request.POST.get("water_changed"))
        notes = request.POST.get("notes", "").strip()

        errors = []
        selected_batch = batches.filter(pk=batch_id).first() if batch_id else None
        selected_report = assigned_sick_reports.filter(pk=sickness_report_id).first() if sickness_report_id else None
        if selected_report and selected_report.batch:
            selected_batch = selected_report.batch

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

        if not selected_batch and not selected_report:
            errors.append("Please select a valid batch or sickbay case from your assignment.")

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
                sickness_report=selected_report,
            )
            messages.success(request, "Sickbay: Cleaning record saved.")
            return redirect("sick_record_cleaning")

    from django.db.models import Q
    recent_cleaning_records = CleaningRecord.objects.filter(
        recorded_by=request.user,
        record_date=date.today(),
    ).filter(Q(batch__in=batches) | Q(sickness_report__in=assigned_sick_reports)).select_related("batch__house")[:7]

    return render(request, "record_cleaning_sickbay.html", {"batches": batches, "today": date.today(), "recent_cleaning_records": recent_cleaning_records, "sick_reports": assigned_sick_reports})


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
