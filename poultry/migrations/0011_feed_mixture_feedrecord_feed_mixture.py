from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("poultry", "0010_egg_collection_average_egg_weight_g"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FeedMixture",
            fields=[
                ("mixture_id", models.BigAutoField(primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=120)),
                ("mix_date", models.DateField(db_index=True)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "mixed_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="feed_mixtures_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-mix_date", "-created_at"],
                "indexes": [
                    models.Index(fields=["mix_date"], name="poultry_fee_mix_dat_e17981_idx"),
                    models.Index(fields=["mixed_by", "mix_date"], name="poultry_fee_mixed__8c57f4_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="FeedMixtureIngredient",
            fields=[
                ("ingredient_id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "feed_type",
                    models.CharField(
                        choices=[
                            ("STARTER", "Starter"),
                            ("GROWER", "Grower"),
                            ("LAYER_MASH", "Layer Mash"),
                            ("OTHER", "Other"),
                        ],
                        default="OTHER",
                        max_length=20,
                    ),
                ),
                ("ingredient_name", models.CharField(blank=True, max_length=120)),
                (
                    "quantity_kg",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=10,
                        validators=[django.core.validators.MinValueValidator(Decimal("0.00"))],
                    ),
                ),
                (
                    "mixture",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ingredients",
                        to="poultry.feedmixture",
                    ),
                ),
            ],
            options={"ordering": ["ingredient_id"]},
        ),
        migrations.CreateModel(
            name="FeedMixtureAllocation",
            fields=[
                ("allocation_id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "quantity_kg",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=10,
                        validators=[django.core.validators.MinValueValidator(Decimal("0.00"))],
                    ),
                ),
                (
                    "house",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="feed_mixture_allocations",
                        to="poultry.poultryhouse",
                    ),
                ),
                (
                    "mixture",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="allocations",
                        to="poultry.feedmixture",
                    ),
                ),
            ],
            options={
                "ordering": ["house__house_code", "allocation_id"],
                "unique_together": {("mixture", "house")},
            },
        ),
        migrations.AddField(
            model_name="feedrecord",
            name="feed_mixture",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="feed_records",
                to="poultry.feedmixture",
            ),
        ),
    ]
