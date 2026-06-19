from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hr", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="WelfareRequest",
            fields=[
                ("request_id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "request_type",
                    models.CharField(
                        choices=[
                            ("LEAVE", "Leave"),
                            ("SALARY_ADVANCE", "Salary Advance"),
                            ("GENERAL", "General Welfare"),
                        ],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                ("title", models.CharField(max_length=120)),
                ("details", models.TextField()),
                ("leave_start", models.DateField(blank=True, null=True)),
                ("leave_end", models.DateField(blank=True, null=True)),
                (
                    "advance_amount",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=14,
                        null=True,
                        validators=[django.core.validators.MinValueValidator(Decimal("0.00"))],
                    ),
                ),
                ("currency", models.CharField(default="UGX", max_length=10)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("SUBMITTED", "Submitted to Supervisor"),
                            ("SUPERVISOR_APPROVED", "Sent to Manager"),
                            ("SUPERVISOR_REJECTED", "Rejected by Supervisor"),
                            ("MANAGER_APPROVED", "Approved by Manager"),
                            ("MANAGER_REJECTED", "Rejected by Manager"),
                        ],
                        db_index=True,
                        default="SUBMITTED",
                        max_length=24,
                    ),
                ),
                ("supervisor_notes", models.TextField(blank=True)),
                ("supervisor_reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("manager_notes", models.TextField(blank=True)),
                ("manager_reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "manager",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="welfare_requests_managed",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "supervisor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="welfare_requests_supervised",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "worker",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="welfare_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-request_id"],
                "indexes": [
                    models.Index(fields=["worker", "status"], name="hr_welfare_worker__736ada_idx"),
                    models.Index(fields=["request_type", "status"], name="hr_welfare_request_6ddfee_idx"),
                    models.Index(fields=["created_at"], name="hr_welfare_created_e5d51f_idx"),
                ],
            },
        ),
    ]
