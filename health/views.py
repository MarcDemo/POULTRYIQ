from datetime import date

from django.contrib import messages
from django.shortcuts import redirect, render

from accounts.decorators import worker_required
from poultry.models import MortalityCause, MortalityRecord, PoultryBatch

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

        errors = []
        selected_batch = worker_batches.filter(pk=batch_id).first() if batch_id else None

        if not selected_batch:
            errors.append("Please select a valid batch from your assigned houses.")

        try:
            record_date = date.fromisoformat(record_date_raw)
        except ValueError:
            record_date = None
            errors.append("Please provide a valid date.")

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

def treatment(request):
    return render(request, 'treatment.html')

def vaccination(request):
    return render(request, 'vaccination.html')

def vaccine_report(request):
    vaccinations = [
        {'date': '5th april', 'house': 'house B', 'vaccine': 'newcastle vaccine', 'status': 'scheduled'},
        {'date': '4th april', 'house': 'house A', 'vaccine': 'gumboro vaccine', 'status': 'done'},
    ]
    return render(request, 'vaccine_report.html', {'vaccinations': vaccinations})