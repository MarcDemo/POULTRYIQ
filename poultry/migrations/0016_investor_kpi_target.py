import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("poultry", "0015_feed_formulas"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="InvestorKpiTarget",
            fields=[
                ("target_id", models.BigAutoField(primary_key=True, serialize=False)),
                ("metric_key", models.CharField(db_index=True, max_length=80)),
                ("target_value", models.DecimalField(decimal_places=4, max_digits=18)),
                (
                    "direction",
                    models.CharField(
                        choices=[("MINIMUM", "At least"), ("MAXIMUM", "At most")],
                        max_length=10,
                    ),
                ),
                ("effective_from", models.DateField(db_index=True)),
                ("effective_to", models.DateField(blank=True, db_index=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="investor_kpi_targets_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["metric_key", "-effective_from", "-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="investorkpitarget",
            constraint=models.UniqueConstraint(
                fields=("metric_key", "effective_from"),
                name="uniq_investor_kpi_target_date",
            ),
        ),
        migrations.AddConstraint(
            model_name="investorkpitarget",
            constraint=models.CheckConstraint(
                condition=models.Q(("effective_to__isnull", True), ("effective_to__gte", models.F("effective_from")), _connector="OR"),
                name="investor_kpi_target_dates_valid",
            ),
        ),
        migrations.AddIndex(
            model_name="investorkpitarget",
            index=models.Index(
                fields=["metric_key", "effective_from", "effective_to"],
                name="poultry_kpi_target_period_idx",
            ),
        ),
    ]
