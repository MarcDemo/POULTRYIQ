from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0002_salarypayment_payroll_breakdown"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="salarypayment",
            name="bonus_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=14,
                validators=[django.core.validators.MinValueValidator(Decimal("0.00"))],
            ),
        ),
        migrations.AddField(
            model_name="salarypayment",
            name="edit_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="salarypayment",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.CreateModel(
            name="SalaryBonus",
            fields=[
                ("bonus_id", models.BigAutoField(primary_key=True, serialize=False)),
                ("period_month", models.CharField(db_index=True, max_length=7)),
                (
                    "amount",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=14,
                        validators=[django.core.validators.MinValueValidator(Decimal("0.00"))],
                    ),
                ),
                ("reason", models.TextField()),
                ("granted_at", models.DateTimeField(auto_now_add=True)),
                (
                    "employee",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="salary_bonuses",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "granted_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="salary_bonuses_granted",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-period_month", "employee__username", "-granted_at"],
            },
        ),
        migrations.CreateModel(
            name="SalaryPaymentEditLog",
            fields=[
                ("log_id", models.BigAutoField(primary_key=True, serialize=False)),
                ("reason", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "edited_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="salary_payment_edits",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "salary",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="edit_logs",
                        to="payroll.salarypayment",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-log_id"],
            },
        ),
    ]
