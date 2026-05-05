from django.db import models
from django.conf import settings
from django.utils import timezone


class AlertType(models.Model):
    """
    Categories of alerts/messages:
    - SYSTEM: automated alerts from the system (e.g., time to change bedding)
    - DIRECT: direct message from supervisor/manager to worker/other user
    - MAINTENANCE: maintenance task reminders
    - HEALTH: health/vaccination alerts
    """

    class AlertTypeCode(models.TextChoices):
        SYSTEM = "SYSTEM", "System Alert"
        DIRECT = "DIRECT", "Direct Message"
        MAINTENANCE = "MAINTENANCE", "Maintenance Task"
        HEALTH = "HEALTH", "Health Alert"
        COMPLIANCE = "COMPLIANCE", "Compliance Alert"

    code = models.CharField(max_length=30, choices=AlertTypeCode.choices, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Alert(models.Model):
    """
    Unified model for both system-generated alerts and direct messages.
    - System alerts: sender is system/admin, receiver is assigned user(s)
    - Direct messages: supervisor/manager sends to worker(s)
    """

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        URGENT = "URGENT", "Urgent"

    class Status(models.TextChoices):
        UNREAD = "UNREAD", "Unread"
        READ = "READ", "Read"
        ACKNOWLEDGED = "ACKNOWLEDGED", "Acknowledged"
        RESOLVED = "RESOLVED", "Resolved"

    # Basic fields
    alert_id = models.BigAutoField(primary_key=True)
    alert_type = models.ForeignKey(AlertType, on_delete=models.PROTECT, related_name="alerts")

    # Content
    title = models.CharField(max_length=200)
    message = models.TextField()

    # Sender info
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_alerts",
        help_text="Null if system-generated"
    )
    
    # Receiver info
    receiver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="received_alerts"
    )

    # Status tracking
    priority = models.CharField(max_length=20, choices=Priority.choices, default="MEDIUM")
    status = models.CharField(max_length=20, choices=Status.choices, default="UNREAD")

    # Timing
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    read_at = models.DateTimeField(null=True, blank=True)
    due_date = models.DateTimeField(null=True, blank=True, help_text="When action should be taken")
    persist_until_resolved = models.BooleanField(
        default=False,
        help_text="Keep this alert active in reminder counts until it is explicitly resolved.",
    )

    # Context (optional)
    related_house = models.ForeignKey(
        'poultry.PoultryHouse',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="If alert is related to specific poultry house"
    )
    related_batch = models.ForeignKey(
        'poultry.PoultryBatch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="If alert is related to specific batch"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["receiver", "-created_at"]),
            models.Index(fields=["status", "receiver"]),
            models.Index(fields=["priority", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"[{self.get_priority_display()}] {self.title}"

    def mark_as_read(self):
        """Mark alert as read"""
        if self.persist_until_resolved and self.status != self.Status.RESOLVED:
            return
        if self.status == self.Status.UNREAD:
            self.status = self.Status.READ
            self.read_at = timezone.now()
            self.save(update_fields=["status", "read_at", "updated_at"])

    def mark_as_acknowledged(self):
        """Mark alert as acknowledged by receiver"""
        self.status = self.Status.ACKNOWLEDGED
        self.save(update_fields=["status", "updated_at"])

    def mark_as_resolved(self):
        """Mark alert as resolved (action taken)"""
        self.status = self.Status.RESOLVED
        self.save(update_fields=["status", "updated_at"])

    @property
    def is_system_alert(self) -> bool:
        """Check if this is a system-generated alert"""
        return self.alert_type.code == AlertType.AlertTypeCode.SYSTEM

    @property
    def is_overdue(self) -> bool:
        """Check if alert action is overdue"""
        if self.due_date and self.status != self.Status.RESOLVED:
            return timezone.now() > self.due_date
        return False


class AlertTemplate(models.Model):
    """
    Reusable templates for common system alerts.
    E.g., "Time to change bedding", "Water system check due"
    """

    template_id = models.BigAutoField(primary_key=True)
    alert_type = models.ForeignKey(AlertType, on_delete=models.PROTECT)

    code = models.CharField(max_length=50, unique=True)  # e.g., "BEDDING_CHANGE"
    name = models.CharField(max_length=100)
    title_template = models.CharField(max_length=200)
    message_template = models.TextField()

    frequency_days = models.IntegerField(
        null=True,
        blank=True,
        help_text="If set, alert repeats every N days"
    )
    priority = models.CharField(
        max_length=20,
        choices=Alert.Priority.choices,
        default="MEDIUM"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def create_alert(self, receiver, **context):
        """
        Create an alert from this template.
        context dict can contain variables for title/message substitution
        """
        title = self.title_template.format(**context)
        message = self.message_template.format(**context)

        return Alert.objects.create(
            alert_type=self.alert_type,
            title=title,
            message=message,
            receiver=receiver,
            priority=self.priority,
            sender=None  # System alert
        )


class AlertSchedule(models.Model):
    """
    Track when system alerts should be sent to users.
    Useful for recurring maintenance/health check reminders.
    """

    schedule_id = models.BigAutoField(primary_key=True)
    template = models.ForeignKey(AlertTemplate, on_delete=models.CASCADE)
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    # Related context
    related_house = models.ForeignKey(
        'poultry.PoultryHouse',
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )

    # Schedule
    last_alert_sent = models.DateTimeField(null=True, blank=True)
    next_alert_due = models.DateTimeField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["next_alert_due"]
        indexes = [models.Index(fields=["is_active", "next_alert_due"])]

    def __str__(self) -> str:
        return f"{self.template.name} for {self.receiver.display_name}"

    @property
    def is_due(self) -> bool:
        """Check if alert is due to be sent"""
        return self.is_active and timezone.now() >= self.next_alert_due
