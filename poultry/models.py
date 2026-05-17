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
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))], null=True, blank=True)

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


class ApprovalStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"


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

    status = models.CharField(
        max_length=10,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="mortality_reviews"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

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
    # optional link to a sickbay case when this collection is for a sickness report
    sickness_report = models.ForeignKey(
        'health.SicknessReport', on_delete=models.PROTECT, null=True, blank=True, related_name='sickbay_egg_collections'
    )
    collection_date = models.DateField(db_index=True)

    eggs_collected = models.PositiveIntegerField(validators=[MinValueValidator(0)])
    eggs_rejected = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])

    notes = models.TextField(blank=True)

    status = models.CharField(
        max_length=10,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="egg_reviews"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    collected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="egg_collections"
    )
    collected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-collection_date", "-collection_id"]
        indexes = [
                models.Index(fields=["batch", "collection_date"]),
                models.Index(fields=["sickness_report", "collection_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.batch.batch_code} egg collection {self.collection_date}: {self.eggs_collected}"


class FeedRecord(models.Model):
    class FeedType(models.TextChoices):
        STARTER = "STARTER", "Starter"
        GROWER = "GROWER", "Grower"
        LAYER_MASH = "LAYER_MASH", "Layer Mash"
        OTHER = "OTHER", "Other"

    feed_id = models.BigAutoField(primary_key=True)
    batch = models.ForeignKey(PoultryBatch, on_delete=models.PROTECT, related_name="feed_records")
    sickness_report = models.ForeignKey(
        'health.SicknessReport', on_delete=models.PROTECT, null=True, blank=True, related_name='sickbay_feed_records'
    )
    record_date = models.DateField(db_index=True)
    feed_type = models.CharField(max_length=20, choices=FeedType.choices, default=FeedType.OTHER)
    quantity_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    time_given = models.TimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    status = models.CharField(
        max_length=10,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="feed_reviews"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="feed_records_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-record_date", "-feed_id"]
        indexes = [
            models.Index(fields=["batch", "record_date"]),
            models.Index(fields=["sickness_report", "record_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.batch.batch_code} feed {self.record_date}: {self.quantity_kg}kg"


class CleaningRecord(models.Model):
    cleaning_id = models.BigAutoField(primary_key=True)
    batch = models.ForeignKey(PoultryBatch, on_delete=models.PROTECT, related_name="cleaning_records")
    sickness_report = models.ForeignKey(
        'health.SicknessReport', on_delete=models.PROTECT, null=True, blank=True, related_name='sickbay_cleaning_records'
    )
    record_date = models.DateField(db_index=True)

    house_cleaned = models.BooleanField(default=False)
    disinfection_done = models.BooleanField(default=False)
    water_changed = models.BooleanField(default=False)

    notes = models.TextField(blank=True)

    status = models.CharField(
        max_length=10,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="cleaning_reviews"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cleaning_records_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-record_date", "-cleaning_id"]
        indexes = [
            models.Index(fields=["batch", "record_date"]),
            models.Index(fields=["sickness_report", "record_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.batch.batch_code} cleaning {self.record_date}"


class CleaningPhoto(models.Model):
    """
    Photos uploaded as proof for a cleaning record.
    Multiple photos can be attached to a single CleaningRecord.
    """
    photo_id = models.BigAutoField(primary_key=True)
    cleaning_record = models.ForeignKey(
        CleaningRecord, on_delete=models.CASCADE, related_name="photos"
    )
    image = models.ImageField(upload_to="cleaning_photos/%Y/%m/%d/")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cleaning_photos_uploaded"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Photo {self.photo_id} for {self.cleaning_record}"