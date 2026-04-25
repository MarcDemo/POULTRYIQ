from django.shortcuts import render
from .models import PoultryBatch, DailyProduction,PoultryHouse,egg_collection
from .forms import PoultryBatchForm
from django.shortcuts import render, redirect
from django.contrib import messages
from django.db.models import Sum
from django.utils.timezone import now
from datetime import date

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

def record_feed(request):
    return render(request, 'record_feed.html')

def record_egg(request):
    return render(request, 'record_egg.html')

def record_cleaning(request):
    return render(request, 'record_cleaning.html')

def workersdash(request):
    user = request.user
    house = getattr(user, "house", None)  # ✅ correct

    total_birds = 0

    if house:
        batches = PoultryBatch.objects.filter(
            house=house,
            status="ACTIVE"
        ).prefetch_related("mortality_records")

        for batch in batches:
            mortality = batch.mortality_records.aggregate(
                total=Sum("number_dead")
            )["total"] or 0

            total_birds += (batch.initial_quantity - mortality)

    context = {
        "house": house,   # ✅ pass correct variable
        "total_birds": total_birds
    }

    return render(request, "workersdash.html", context)
   

def supdash(request):
    return render(request, 'supdash.html')

def supapproval(request):
    return render(request, 'supapproval.html')

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