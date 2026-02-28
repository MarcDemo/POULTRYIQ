from django.db import models
from django.contrib.auth.models import AbstractUser

# Create your models here.

class Role(models.Model):
    
    class RoleCode(models.TextChoices):
        WORKER = "WORKER", "Farm Worker"
        MANAGER = "MANAGER", "Farm Manager"
        OWNER = "OWNER", "Business Owner"

    code = models.CharField(max_length=20, choices=RoleCode.choices, unique=True)
    name = models.CharField(max_length=80)

    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name


class User(AbstractUser):
    """
    Custom User model using username/password login.
    Phone is optional (future OTP, WhatsApp alerts, etc.).
    """
    role = models.ForeignKey(Role, on_delete=models.PROTECT, null=True, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)

    
    is_locked = models.BooleanField(default=False)

    def __str__(self) -> str:
        return self.username
