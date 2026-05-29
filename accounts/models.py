from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.conf import settings
from django.core.validators import MinValueValidator
from decimal import Decimal


HOUSE_REQUIRED_ROLE_CODES = {"WORKER", "SUPERVISOR"}


def role_requires_house_assignment(role) -> bool:
    if not role:
        return False

    role_code = (getattr(role, "code", "") or "").upper()
    role_name = (getattr(role, "name", "") or "").strip().lower()

    return (
        role_code in HOUSE_REQUIRED_ROLE_CODES
        or "worker" in role_name
        or "supervisor" in role_name
    )


def validate_house_assignment(role, houses) -> None:
    if not role_requires_house_assignment(role):
        return

    try:
        house_count = houses.count()
    except (AttributeError, TypeError):
        house_count = len(list(houses or []))

    if house_count == 0:
        raise ValidationError(
            "Workers and supervisors must be assigned to at least one poultry house."
        )


class Role(models.Model):

    class RoleCode(models.TextChoices):
        WORKER = "WORKER", "Farm Worker"
        SUPERVISOR = "SUPERVISOR", "Farm Supervisor"
        MANAGER = "MANAGER", "Farm Manager"
        OWNER = "OWNER", "Business Owner"

    code = models.CharField(max_length=20, choices=RoleCode.choices, unique=True)
    name = models.CharField(max_length=80)

    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name


class User(AbstractUser):

    role = models.ForeignKey(Role, on_delete=models.PROTECT, null=True, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    is_locked = models.BooleanField(default=False)

    houses = models.ManyToManyField(
        'poultry.PoultryHouse',
        blank=True,
        related_name='assigned_users'
    )

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        full_name = self.get_full_name().strip()
        return full_name or self.username

    @property
    def role_code(self) -> str:
        return self.role.code if self.role else ""

    @property
    def is_worker(self) -> bool:
        return self.role_code == "WORKER"

    @property
    def is_supervisor(self) -> bool:
        return self.role_code == "SUPERVISOR"

    @property
    def is_manager(self) -> bool:
        return self.role_code == "MANAGER"

    @property
    def is_investor(self) -> bool:
        role_name = (self.role.name or "").strip().lower() if self.role else ""
        return self.role_code in {"OWNER", "INVESTOR"} or "investor" in role_name

    @property
    def can_authenticate(self) -> bool:
        return self.is_active and not self.is_locked

    @property
    def requires_house_assignment(self) -> bool:
        return role_requires_house_assignment(self.role)

    def clean(self) -> None:
        super().clean()

        if self.pk and self.requires_house_assignment:
            validate_house_assignment(self.role, self.houses.all())


class InvestorCapitalTransaction(models.Model):
    class TransactionType(models.TextChoices):
        STARTUP = "STARTUP", "Startup Capital"
        ADDITION = "ADDITION", "Additional Capital"
        WITHDRAWAL = "WITHDRAWAL", "Owner Withdrawal"

    transaction_id = models.BigAutoField(primary_key=True)
    transaction_type = models.CharField(
        max_length=12,
        choices=TransactionType.choices,
        default=TransactionType.ADDITION,
        db_index=True,
    )
    transaction_date = models.DateField(db_index=True)
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    notes = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="investor_capital_transactions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-transaction_date", "-created_at"]
        indexes = [
            models.Index(fields=["transaction_type", "transaction_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_transaction_type_display()} - {self.amount}"
