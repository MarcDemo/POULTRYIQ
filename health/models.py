from django.db import models
from decimal import Decimal
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator
from django.db.models import Sum, Count


# Create your models here.

User = get_user_model()



class HealthEvent(models.Model):
    """
    A health intervention/event for a batch: treatment, vaccination, vet visit, disease incident, etc.
    Lean but traceable. Costs can be captured via ExpenseTransaction (category=VET/HEALTH)
    and allocated to the batch using ExpenseAllocation.
    """
    class EventType(models.TextChoices):
        TREATMENT = "TREATMENT", "Treatment"
        VACCINATION = "VACCINATION", "Vaccination"
        VET_VISIT = "VET_VISIT", "Vet Visit"
        DISEASE = "DISEASE", "Disease/Outbreak"
        OTHER = "OTHER", "Other"

    event_id = models.BigAutoField(primary_key=True)
    batch = models.ForeignKey(
        "poultry.PoultryBatch", on_delete=models.PROTECT, related_name="health_events"
    )

    event_date = models.DateField(db_index=True)
    event_type = models.CharField(max_length=20, choices=EventType.choices, db_index=True)

    title = models.CharField(max_length=150)  # e.g. "Newcastle vaccination", "Coccidiosis treatment"
    symptoms_or_reason = models.TextField(blank=True)
    action_taken = models.TextField(blank=True)
    outcome = models.TextField(blank=True)

    # Who administered / supervised
    administered_by_name = models.CharField(max_length=120, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="health_events_recorded"
    )
    recorded_at = models.DateTimeField(auto_now_add=True)

    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-event_date", "-event_id"]
        indexes = [
            models.Index(fields=["batch", "event_date"]),
            models.Index(fields=["event_type", "event_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.batch.batch_code} {self.event_type} {self.event_date}"


class HealthEventItem(models.Model):
    """
    Optional detail lines for drugs/vaccines used in a HealthEvent.
    
    """
    line_id = models.BigAutoField(primary_key=True)
    event = models.ForeignKey(HealthEvent, on_delete=models.CASCADE, related_name="items")

    item_name = models.CharField(max_length=150)  # e.g. "Vaccine X", "Antibiotic Y"
    quantity = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.000"))]
    )
    unit = models.CharField(max_length=20, blank=True)  # ml, pcs, sachets
    dosage_note = models.CharField(max_length=255, blank=True)  # e.g. "1ml per bird", "as per vet"

    notes = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["event"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_id} - {self.item_name}"
    

class SicknessReport(models.Model):
    class CaseStatus(models.TextChoices):
        REPORTED = "REPORTED", "Awaiting Vet Diagnosis"
        DIAGNOSED = "DIAGNOSED", "Treatment Planned"
        TREATMENT_COMPLETED = "TREATMENT_COMPLETED", "Treatment Given"

    ACTION_CHOICES = [
        ('sickbay','Moved to Sickbay'),
        ('crowd','Left in Crowd'),
    ]

    date = models.DateField()
    house = models.CharField(max_length=100)
    house_ref = models.ForeignKey(
        "poultry.PoultryHouse",
        on_delete=models.PROTECT,
        related_name="sickness_reports",
        null=True,
        blank=True,
    )
    batch = models.ForeignKey(
        "poultry.PoultryBatch",
        on_delete=models.PROTECT,
        related_name="sickness_reports",
        null=True,
        blank=True,
    )
    bird_identifier = models.CharField(max_length=80, blank=True)
    symptoms = models.TextField(blank=True)

    disease = models.CharField(max_length=255, blank=True)
    vet_name = models.CharField(max_length=120, blank=True)
    vet_visit_date = models.DateField(null=True, blank=True)
    diagnosis_notes = models.TextField(blank=True)
    case_status = models.CharField(
        max_length=30,
        choices=CaseStatus.choices,
        default=CaseStatus.REPORTED,
        db_index=True,
    )
    treatment_completed_at = models.DateTimeField(null=True, blank=True)

    affected = models.IntegerField()

    image = models.ImageField(upload_to='sickness/', blank=True, null=True)

    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    isolated = models.BooleanField(default=False)
    isolation_name = models.CharField(max_length=255, blank=True)

    notes = models.TextField(blank=True)

    reported_by = models.ForeignKey(User, on_delete=models.CASCADE)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    @property
    def house_label(self) -> str:
        if self.house_ref:
            return self.house_ref.name or self.house_ref.house_code
        return self.house

    @property
    def case_label(self) -> str:
        identifier = (self.bird_identifier or "").strip()
        if identifier:
            return identifier
        bird_count = self.affected or 0
        if bird_count == 1:
            return "1 bird"
        return f"{bird_count} birds"

    @property
    def diagnosis_label(self) -> str:
        return (self.disease or "").strip() or "Pending vet diagnosis"

    @property
    def is_isolation_case(self) -> bool:
        return self.action == "sickbay"

    def _build_isolation_name(self) -> str:
        house_name = self.house_label or "House"
        disease_name = (self.disease or "General").strip() or "General"
        return f"{house_name}-Isolation({disease_name})"

    def save(self, *args, **kwargs):
        if self.house_ref:
            self.house = self.house_ref.name or self.house_ref.house_code
        self.isolated = self.is_isolation_case
        self.isolation_name = self._build_isolation_name() if self.isolated else ""
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.case_label} {self.house_label} sickness {self.date}"


class SickbayCleaningRecord(models.Model):
    cleaning_id = models.BigAutoField(primary_key=True)
    sickness_report = models.ForeignKey(
        SicknessReport,
        on_delete=models.PROTECT,
        related_name="sickbay_cleanings",
    )
    record_date = models.DateField(db_index=True)
    sickbay_cleaned = models.BooleanField(default=False)
    disinfection_done = models.BooleanField(default=False)
    equipment_cleaned = models.BooleanField(default=False)
    water_changed = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sickbay_cleaning_records",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-record_date", "-cleaning_id"]
        indexes = [
            models.Index(fields=["record_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.sickness_report.isolation_name or self.sickness_report.house_label} cleaning {self.record_date}"


class TreatmentPlanItem(models.Model):
    plan_id = models.BigAutoField(primary_key=True)
    sickness_report = models.ForeignKey(
        SicknessReport,
        on_delete=models.CASCADE,
        related_name="treatment_items",
    )
    medicine_name = models.CharField(max_length=150)
    dosage = models.CharField(max_length=120)
    administration_route = models.CharField(max_length=80, blank=True)
    instructions = models.TextField(blank=True)
    scheduled_for = models.DateTimeField(null=True, blank=True, db_index=True)
  
    start_datetime = models.DateTimeField(null=True, blank=True)
    duration_days = models.PositiveIntegerField(null=True, blank=True)
    frequency_hours = models.PositiveIntegerField(null=True, blank=True)
    is_given = models.BooleanField(default=False, db_index=True)
    given_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="treatment_plan_items_created",
    )
    marked_given_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="treatment_plan_items_given",
    )
    alert = models.ForeignKey(
        "alerts.Alert",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="treatment_plan_items",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["is_given", "scheduled_for", "-created_at"]
        indexes = [
            models.Index(fields=["is_given", "scheduled_for"]),
        ]

    @property
    def has_active_alert(self) -> bool:
        return bool(self.alert_id and self.alert.status != "RESOLVED")

    def __str__(self) -> str:
        return f"{self.sickness_report.case_label} - {self.medicine_name}"


class VaccinationSchedule(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Scheduled"
        ADMINISTERED = "ADMINISTERED", "Administered"

    schedule_id = models.BigAutoField(primary_key=True)
    house_ref = models.ForeignKey(
        "poultry.PoultryHouse",
        on_delete=models.PROTECT,
        related_name="vaccination_schedules",
        null=True,
        blank=True,
    )
    batch = models.ForeignKey(
        "poultry.PoultryBatch",
        on_delete=models.PROTECT,
        related_name="vaccination_schedules",
        null=True,
        blank=True,
    )
    vaccine_name = models.CharField(max_length=150)
    brand = models.CharField(max_length=150, blank=True)
    dosage = models.CharField(max_length=120, blank=True)
    administration_mode = models.CharField(max_length=120, blank=True)
    frequency_days = models.PositiveIntegerField(null=True, blank=True)
    scheduled_for = models.DateTimeField(db_index=True)
    vet_name = models.CharField(max_length=120, blank=True)
    brand_expiry_date = models.DateField(null=True, blank=True)
    number_vaccinated = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED, db_index=True)
    administered_at = models.DateTimeField(null=True, blank=True)
    administered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vaccinations_administered",
    )
    alert = models.ForeignKey(
        "alerts.Alert",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vaccination_schedules",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="vaccination_schedules_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-scheduled_for", "-created_at"]
        indexes = [
            models.Index(fields=["scheduled_for"]),
            models.Index(fields=["status", "scheduled_for"]),
        ]

    def is_due(self):
        from django.utils import timezone

        if not self.scheduled_for:
            return False
        return self.scheduled_for <= timezone.now()

    def is_overdue(self):
        from django.utils import timezone

        if not self.scheduled_for:
            return False
        return self.scheduled_for < timezone.now() and self.status == self.Status.SCHEDULED

    def __str__(self) -> str:
        return f"{self.vaccine_name} for {self.house_ref or self.batch} on {self.scheduled_for.date()}"
