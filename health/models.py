from django.db import models
from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator

# Create your models here.




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