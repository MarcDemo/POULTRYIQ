from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0005_supplier_tin_number"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="InventoryRequisition",
            fields=[
                ("requisition_id", models.BigAutoField(primary_key=True, serialize=False)),
                ("item_name", models.CharField(blank=True, max_length=120)),
                (
                    "quantity",
                    models.DecimalField(
                        decimal_places=3,
                        max_digits=12,
                        validators=[django.core.validators.MinValueValidator(Decimal("0.000"))],
                    ),
                ),
                ("unit", models.CharField(default="kg", max_length=20)),
                ("needed_by", models.DateField(blank=True, null=True)),
                ("reason", models.TextField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("SUBMITTED", "Submitted to Manager"),
                            ("APPROVED", "Approved"),
                            ("REJECTED", "Rejected"),
                        ],
                        db_index=True,
                        default="SUBMITTED",
                        max_length=12,
                    ),
                ),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("review_notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "item",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="requisitions",
                        to="inventory.item",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_requisitions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_requisitions_reviewed",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-requisition_id"],
                "indexes": [
                    models.Index(fields=["requested_by", "status"], name="inventory_r_request_87a7a3_idx"),
                    models.Index(fields=["status", "created_at"], name="inventory_r_status_4696d5_idx"),
                ],
            },
        ),
    ]
