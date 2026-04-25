from django.db import models
from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.shortcuts import render, redirect
from django.utils.timezone import now
from datetime import date,timedelta

# Create your models here.


from datetime import date  # 👈 make sure this is at the top

class PoultryBatch(models.Model):

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        CLOSED = "CLOSED", "Closed"

    batch_id = models.BigAutoField(primary_key=True)
    batch_code = models.CharField(max_length=50, unique=True, db_index=True)

    house = models.ForeignKey(
        'poultry.PoultryHouse',
        on_delete=models.PROTECT,
        related_name='batches'
    )

    breed = models.CharField(max_length=100, blank=True)
    supplier_name = models.CharField(max_length=150, blank=True)

    date_stocked = models.DateField(db_index=True)
    initial_quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    initial_age_days = models.PositiveIntegerField()  # 👈 make sure this exists

    expected_lay_start = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="batches_created"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.batch_code} ({self.house.house_code})"

    # 👇👇 PUT IT HERE (inside the class)
    @property
    def current_age_days(self):
        if not self.date_stocked:
            return self.initial_age_days

        days_passed = (date.today() - self.date_stocked).days

        return self.initial_age_days + days_passed
    
    @property
    def expected_lay_date(self):
        """
        Estimate when birds will start laying based on age.
        Assumes laying starts at 20 weeks.
        """

        LAY_START_WEEK = 140

        if not self.date_stocked:
            return None

        # current age
        days_passed = (date.today() - self.date_stocked).days
        current_age = self.initial_age_days + (days_passed )

        weeks_to_lay = LAY_START_WEEK - current_age

        if weeks_to_lay <= 0:
            return "Already laying / expected soon"

        return self.date_stocked + timedelta(days=weeks_to_lay)

    def save(self, *args, **kwargs):
        if not self.batch_code:
            date_part = now().strftime("%Y%m%d")

            # count existing batches today
            count_today = PoultryBatch.objects.filter(
                created_at__date=now().date()
            ).count() + 1

            self.batch_code = f"BT-{date_part}-{count_today:03d}"

        super().save(*args, **kwargs)

def add_batch(request):
    if request.method == "POST":
        form = PoultryBatchForm(request.POST)
        if form.is_valid():
            batch = form.save(commit=False)
            batch.created_by = request.user  #  assign logged-in user
            batch.save()
            return redirect("birds")  
    else:
        form = PoultryBatchForm()

    return render(request, "addbatch.html", {"form": form})


class DailyProduction(models.Model):
    """
    Daily production and flock status record.
    Stores raw data; KPIs are derived later.
    """
    production_id = models.BigAutoField(primary_key=True)
    batch = models.ForeignKey(PoultryBatch, on_delete=models.PROTECT, related_name="daily_production")

    record_date = models.DateField(db_index=True)

    birds_alive = models.PositiveIntegerField(validators=[MinValueValidator(0)])
    eggs_collected = models.PositiveIntegerField(validators=[MinValueValidator(0)])

    # for Damaged eggs, dont get confused Shan
    eggs_rejected = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])

    remarks = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="daily_production_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-record_date", "-production_id"]
        unique_together = ("batch", "record_date")
        indexes = [
            models.Index(fields=["batch", "record_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.batch.batch_code} - {self.record_date}"


class MortalityCause(models.Model):
    """
    Controlled list of mortality causes, with ability to expand.
    """
    cause_id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=120, unique=True)  # e.g., Heat stress, Disease, Injury, Unknown
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name


class MortalityRecord(models.Model):
    """
    Daily mortality record (raw data).
    """
    mortality_id = models.BigAutoField(primary_key=True)
    batch = models.ForeignKey(PoultryBatch, on_delete=models.PROTECT, related_name="mortality_records")
    record_date = models.DateField(db_index=True)

    number_dead = models.PositiveIntegerField(validators=[MinValueValidator(0)])
    cause = models.ForeignKey(MortalityCause, on_delete=models.PROTECT, null=True, blank=True)

    notes = models.TextField(blank=True)

    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="mortality_reported"
    )
    reported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-record_date", "-mortality_id"]
        indexes = [
            models.Index(fields=["batch", "record_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.batch.batch_code} mortality {self.record_date}: {self.number_dead}"

class PoultryHouse(models.Model):
    """
    Represents a physical poultry house/unit.
    """

    house_id = models.BigAutoField(primary_key=True)
    house_code = models.CharField(max_length=50, unique=True)  # e.g. HSE-01
    name = models.CharField(max_length=100, blank=True)

    capacity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="Maximum number of birds this house can hold"
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
    


class egg_collection(models.Model):
    collection_id = models.BigAutoField(primary_key=True)
    batch = models.ForeignKey(PoultryBatch, on_delete=models.PROTECT, related_name="egg_collections")
    collection_date = models.DateField(db_index=True)

    eggs_collected = models.PositiveIntegerField(validators=[MinValueValidator(0)])
    eggs_rejected = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])

    notes = models.TextField(blank=True)

    collected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="egg_collections"
    )
    collected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-collection_date", "-collection_id"]
        indexes = [
            models.Index(fields=["batch", "collection_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.batch.batch_code} egg collection {self.collection_date}: {self.eggs_collected}"