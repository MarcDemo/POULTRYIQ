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


class FlockStage(models.TextChoices):
    CHICK = "CHICK", "Chick (0 - 8 weeks)"
    GROWER = "GROWER", "Grower (8 - 17 weeks)"
    PRE_LAY = "PRE_LAY", "Pre-lay (17 - 20 weeks)"
    LAYER_1 = "LAYER_1", "Layer 1 (20 - 40 weeks)"
    LAYER_2 = "LAYER_2", "Layer 2 (40 weeks to end)"


def flock_stage_for_age_days(age_days):
    age_days = max(int(age_days or 0), 0)
    if age_days < 56:
        return FlockStage.CHICK
    if age_days < 119:
        return FlockStage.GROWER
    if age_days < 140:
        return FlockStage.PRE_LAY
    if age_days < 280:
        return FlockStage.LAYER_1
    return FlockStage.LAYER_2


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

        LAY_START_WEEK = 126

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
    average_egg_weight_g = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        null=True,
        blank=True,
    )

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
    feed_mixture = models.ForeignKey(
        "poultry.FeedMixture",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="feed_records",
    )
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


class InvestorKpiTarget(models.Model):
    class Direction(models.TextChoices):
        MINIMUM = "MINIMUM", "At least"
        MAXIMUM = "MAXIMUM", "At most"

    target_id = models.BigAutoField(primary_key=True)
    metric_key = models.CharField(max_length=80, db_index=True)
    target_value = models.DecimalField(max_digits=18, decimal_places=4)
    direction = models.CharField(max_length=10, choices=Direction.choices)
    effective_from = models.DateField(db_index=True)
    effective_to = models.DateField(null=True, blank=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="investor_kpi_targets_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["metric_key", "-effective_from", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["metric_key", "effective_from"],
                name="uniq_investor_kpi_target_date",
            ),
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=models.F("effective_from")),
                name="investor_kpi_target_dates_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["metric_key", "effective_from", "effective_to"],
                name="poultry_kpi_target_period_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.metric_key}: {self.get_direction_display()} {self.target_value}"


class FeedFormulaTemplate(models.Model):
    formula_id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=120)
    flock_stage = models.CharField(max_length=20, choices=FlockStage.choices, db_index=True)
    concentration_percent = models.PositiveSmallIntegerField(null=True, blank=True)
    reference_weight_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    source = models.CharField(max_length=200, blank=True)
    is_system = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feed_formula_templates_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["is_system", "concentration_percent", "name", "flock_stage"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "flock_stage"],
                condition=models.Q(is_active=True),
                name="uniq_active_feed_formula_stage",
            ),
        ]
        indexes = [
            models.Index(fields=["is_active", "flock_stage"], name="poultry_ff_active_stage_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} - {self.get_flock_stage_display()}"


class FeedFormulaIngredient(models.Model):
    formula_ingredient_id = models.BigAutoField(primary_key=True)
    formula = models.ForeignKey(
        FeedFormulaTemplate,
        on_delete=models.CASCADE,
        related_name="ingredients",
    )
    item = models.ForeignKey(
        "inventory.Item",
        on_delete=models.PROTECT,
        related_name="feed_formula_ingredients",
    )
    quantity_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "formula_ingredient_id"]
        unique_together = ("formula", "item")

    def __str__(self) -> str:
        return f"{self.formula}: {self.item.name} {self.quantity_kg}kg"


class FeedMixture(models.Model):
    mixture_id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=120)
    mix_date = models.DateField(db_index=True)
    formula_template = models.ForeignKey(
        FeedFormulaTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mixtures",
    )
    formula_name_snapshot = models.CharField(max_length=120, blank=True)
    flock_stage = models.CharField(max_length=20, choices=FlockStage.choices, blank=True, db_index=True)
    planned_weight_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        null=True,
        blank=True,
    )
    total_weight_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        null=True,
        blank=True,
    )
    notes = models.TextField(blank=True)
    mixed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="feed_mixtures_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-mix_date", "-created_at"]
        indexes = [
            models.Index(fields=["mix_date"]),
            models.Index(fields=["mixed_by", "mix_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.mix_date})"

    @property
    def total_ingredient_kg(self):
        return self.ingredients.aggregate(total=models.Sum("quantity_kg"))["total"] or Decimal("0.00")

    @property
    def total_allocated_kg(self):
        return self.allocations.aggregate(total=models.Sum("quantity_kg"))["total"] or Decimal("0.00")


class FeedMixtureIngredient(models.Model):
    ingredient_id = models.BigAutoField(primary_key=True)
    mixture = models.ForeignKey(FeedMixture, on_delete=models.CASCADE, related_name="ingredients")
    item = models.ForeignKey(
        "inventory.Item",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="feed_mixture_ingredients",
    )
    feed_type = models.CharField(max_length=20, choices=FeedRecord.FeedType.choices, default=FeedRecord.FeedType.OTHER)
    ingredient_name = models.CharField(max_length=120, blank=True)
    quantity_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )

    class Meta:
        ordering = ["ingredient_id"]

    def __str__(self) -> str:
        label = self.ingredient_name or self.get_feed_type_display()
        return f"{label}: {self.quantity_kg}kg"


class FeedMixtureAllocation(models.Model):
    allocation_id = models.BigAutoField(primary_key=True)
    mixture = models.ForeignKey(FeedMixture, on_delete=models.CASCADE, related_name="allocations")
    house = models.ForeignKey(PoultryHouse, on_delete=models.PROTECT, related_name="feed_mixture_allocations")
    batch = models.ForeignKey(
        PoultryBatch,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="feed_mixture_allocations",
    )
    quantity_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )

    class Meta:
        ordering = ["house__house_code", "allocation_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["mixture", "batch"],
                condition=models.Q(batch__isnull=False),
                name="uniq_feed_mix_batch_alloc",
            ),
        ]

    def __str__(self) -> str:
        target = self.batch.batch_code if self.batch_id else self.house.house_code
        return f"{self.mixture.name} -> {target}: {self.quantity_kg}kg"


class CleaningRecord(models.Model):
    cleaning_id = models.BigAutoField(primary_key=True)
    batch = models.ForeignKey(PoultryBatch, on_delete=models.PROTECT, related_name="cleaning_records")
    sickness_report = models.ForeignKey(
        'health.SicknessReport', on_delete=models.PROTECT, null=True, blank=True, related_name='sickbay_cleaning_records'
    )
    record_date = models.DateField(db_index=True)

    house_cleaned = models.BooleanField(default=False)
    house_raked = models.BooleanField(default=False)
    house_dusted = models.BooleanField(default=False)
    nipples_washed = models.BooleanField(default=False)
    disinfection_done = models.BooleanField(default=False)
    water_changed = models.BooleanField(default=False)

    notes = models.TextField(blank=True)
    notes_audio = models.FileField(upload_to="cleaning_audio/%Y/%m/%d/", blank=True, null=True)

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
    task_key = models.CharField(max_length=32, blank=True)
    image = models.ImageField(upload_to="cleaning_photos/%Y/%m/%d/")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cleaning_photos_uploaded"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Photo {self.photo_id} for {self.cleaning_record}"
