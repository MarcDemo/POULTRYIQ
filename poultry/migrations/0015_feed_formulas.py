from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


FORMULA_SOURCE = "Hendrix / Tunga Nutrition formula supplied by the farm"


def seed_feed_formulas(apps, schema_editor):
    ItemCategory = apps.get_model("inventory", "ItemCategory")
    Item = apps.get_model("inventory", "Item")
    FeedFormulaTemplate = apps.get_model("poultry", "FeedFormulaTemplate")
    FeedFormulaIngredient = apps.get_model("poultry", "FeedFormulaIngredient")

    feed_category, _ = ItemCategory.objects.get_or_create(
        code="FEED",
        defaults={"name": "Feed"},
    )

    item_names = [
        "Layer Concentrate 5%",
        "Layer Concentrate 10%",
        "Layer Concentrate 20%",
        "Stock Feed Lime",
        "Maize",
        "Maize bran",
        "Broken Maize",
        "Soybean Meal",
        "Sunflower Meal",
    ]
    items = {}
    for item_name in item_names:
        item = Item.objects.filter(name__iexact=item_name).first()
        if item is None:
            item = Item.objects.create(
                name=item_name,
                category=feed_category,
                unit="kg",
                is_active=True,
            )
        else:
            changed_fields = []
            if item.category_id != feed_category.pk:
                item.category = feed_category
                changed_fields.append("category")
            if not item.is_active:
                item.is_active = True
                changed_fields.append("is_active")
            if changed_fields:
                item.save(update_fields=changed_fields)
        items[item_name] = item

    recipes = {
        "Hendrix 5%": {
            "concentration": 5,
            "reference_weight": "1000.00",
            "ingredients": [
                ("Layer Concentrate 5%", [60, 50, 50, 50, 50]),
                ("Stock Feed Lime", [20, 20, 90, 95, 100]),
                ("Maize", [180, 200, 180, 185, 175]),
                ("Maize bran", [520, 530, 490, 500, 525]),
                ("Soybean Meal", [120, 100, 100, 95, 90]),
                ("Sunflower Meal", [100, 100, 90, 75, 60]),
            ],
        },
        "Hendrix 10%": {
            "concentration": 10,
            "reference_weight": "1000.00",
            "ingredients": [
                ("Maize bran", [500, 600, 500, 500, 500]),
                ("Broken Maize", [210, 170, 215, 210, 200]),
                ("Soybean Meal", [140, 25, 40, 105, 60]),
                ("Layer Concentrate 10%", [120, 100, 100, 100, 100]),
                ("Sunflower Meal", [20, 95, 100, 0, 50]),
                ("Stock Feed Lime", [10, 10, 45, 85, 90]),
            ],
        },
        "Hendrix 20%": {
            "concentration": 20,
            "reference_weight": "250.00",
            "ingredients": [
                ("Layer Concentrate 20%", [50, 50, 50, 50, 50]),
                ("Stock Feed Lime", [0, 3, 20, 22, 24]),
                ("Maize", [50, 50, 43, 49, 48]),
                ("Maize bran", [125, 140, 130, 129, 128]),
                ("Sunflower Meal", [25, 7, 7, 0, 0]),
            ],
        },
    }
    stages = ["CHICK", "GROWER", "PRE_LAY", "LAYER_1", "LAYER_2"]

    for formula_name, recipe in recipes.items():
        for stage_index, stage in enumerate(stages):
            formula, _ = FeedFormulaTemplate.objects.update_or_create(
                name=formula_name,
                flock_stage=stage,
                defaults={
                    "concentration_percent": recipe["concentration"],
                    "reference_weight_kg": Decimal(recipe["reference_weight"]),
                    "source": FORMULA_SOURCE,
                    "is_system": True,
                    "is_active": True,
                    "created_by": None,
                },
            )
            FeedFormulaIngredient.objects.filter(formula=formula).delete()
            lines = []
            for sort_order, (item_name, quantities) in enumerate(recipe["ingredients"], start=1):
                quantity = Decimal(str(quantities[stage_index]))
                if quantity <= 0:
                    continue
                lines.append(
                    FeedFormulaIngredient(
                        formula=formula,
                        item=items[item_name],
                        quantity_kg=quantity,
                        sort_order=sort_order,
                    )
                )
            FeedFormulaIngredient.objects.bulk_create(lines)


def remove_seeded_feed_formulas(apps, schema_editor):
    FeedFormulaTemplate = apps.get_model("poultry", "FeedFormulaTemplate")
    FeedFormulaTemplate.objects.filter(is_system=True, source=FORMULA_SOURCE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0015_unique_supplier_product_name"),
        ("poultry", "0014_cleaning_evidence_audio_and_tasks"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FeedFormulaTemplate",
            fields=[
                ("formula_id", models.BigAutoField(primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=120)),
                (
                    "flock_stage",
                    models.CharField(
                        choices=[
                            ("CHICK", "Chick (0 - 8 weeks)"),
                            ("GROWER", "Grower (8 - 17 weeks)"),
                            ("PRE_LAY", "Pre-lay (17 - 20 weeks)"),
                            ("LAYER_1", "Layer 1 (20 - 40 weeks)"),
                            ("LAYER_2", "Layer 2 (40 weeks to end)"),
                        ],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                ("concentration_percent", models.PositiveSmallIntegerField(blank=True, null=True)),
                (
                    "reference_weight_kg",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=10,
                        validators=[django.core.validators.MinValueValidator(Decimal("0.01"))],
                    ),
                ),
                ("source", models.CharField(blank=True, max_length=200)),
                ("is_system", models.BooleanField(db_index=True, default=False)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="feed_formula_templates_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["is_system", "concentration_percent", "name", "flock_stage"],
            },
        ),
        migrations.CreateModel(
            name="FeedFormulaIngredient",
            fields=[
                ("formula_ingredient_id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "quantity_kg",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=10,
                        validators=[django.core.validators.MinValueValidator(Decimal("0.01"))],
                    ),
                ),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                (
                    "formula",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ingredients",
                        to="poultry.feedformulatemplate",
                    ),
                ),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="feed_formula_ingredients",
                        to="inventory.item",
                    ),
                ),
            ],
            options={
                "ordering": ["sort_order", "formula_ingredient_id"],
                "unique_together": {("formula", "item")},
            },
        ),
        migrations.AddField(
            model_name="feedmixture",
            name="flock_stage",
            field=models.CharField(
                blank=True,
                choices=[
                    ("CHICK", "Chick (0 - 8 weeks)"),
                    ("GROWER", "Grower (8 - 17 weeks)"),
                    ("PRE_LAY", "Pre-lay (17 - 20 weeks)"),
                    ("LAYER_1", "Layer 1 (20 - 40 weeks)"),
                    ("LAYER_2", "Layer 2 (40 weeks to end)"),
                ],
                db_index=True,
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="feedmixture",
            name="formula_name_snapshot",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="feedmixture",
            name="formula_template",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="mixtures",
                to="poultry.feedformulatemplate",
            ),
        ),
        migrations.AddField(
            model_name="feedmixture",
            name="planned_weight_kg",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=10,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0.00"))],
            ),
        ),
        migrations.AddField(
            model_name="feedmixtureallocation",
            name="batch",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="feed_mixture_allocations",
                to="poultry.poultrybatch",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="feedmixtureallocation",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="feedformulatemplate",
            constraint=models.UniqueConstraint(
                condition=models.Q(is_active=True),
                fields=("name", "flock_stage"),
                name="uniq_active_feed_formula_stage",
            ),
        ),
        migrations.AddConstraint(
            model_name="feedmixtureallocation",
            constraint=models.UniqueConstraint(
                condition=models.Q(batch__isnull=False),
                fields=("mixture", "batch"),
                name="uniq_feed_mix_batch_alloc",
            ),
        ),
        migrations.AddIndex(
            model_name="feedformulatemplate",
            index=models.Index(
                fields=["is_active", "flock_stage"],
                name="poultry_ff_active_stage_idx",
            ),
        ),
        migrations.RunPython(seed_feed_formulas, remove_seeded_feed_formulas),
    ]
