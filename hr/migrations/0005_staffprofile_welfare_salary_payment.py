from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hr", "0004_rename_hr_welfare_worker__736ada_idx_hr_welfarer_worker__542b37_idx_and_more"),
        ("payroll", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StaffProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("profile_photo", models.ImageField(blank=True, upload_to="staff_profiles/")),
                ("age", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("job_title", models.CharField(blank=True, max_length=100)),
                ("employee_number", models.CharField(blank=True, db_index=True, max_length=40)),
                ("tin_number", models.CharField(blank=True, max_length=50)),
                ("nssf_number", models.CharField(blank=True, max_length=50)),
                ("national_id", models.CharField(blank=True, max_length=50)),
                ("next_of_kin_name", models.CharField(blank=True, max_length=150)),
                ("next_of_kin_contact", models.CharField(blank=True, max_length=50)),
                ("physical_address", models.CharField(blank=True, max_length=200)),
                ("emergency_contact", models.CharField(blank=True, max_length=50)),
                (
                    "monthly_salary",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=14,
                        validators=[django.core.validators.MinValueValidator(Decimal("0.00"))],
                    ),
                ),
                (
                    "employment_status",
                    models.CharField(
                        choices=[("ACTIVE", "Active"), ("INACTIVE", "Inactive"), ("ON_LEAVE", "On Leave")],
                        db_index=True,
                        default="ACTIVE",
                        max_length=12,
                    ),
                ),
                ("hire_date", models.DateField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="staff_profile", to=settings.AUTH_USER_MODEL),
                ),
            ],
            options={
                "ordering": ["user__first_name", "user__username"],
                "indexes": [models.Index(fields=["employment_status"], name="hr_staffpro_employm_9cf0ba_idx")],
            },
        ),
        migrations.AddField(
            model_name="welfarerequest",
            name="salary_payment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="deducted_advances",
                to="payroll.salarypayment",
            ),
        ),
    ]
