import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def assign_placeholder_house_to_existing_batches(apps, schema_editor):
    PoultryBatch = apps.get_model("poultry", "PoultryBatch")
    PoultryHouse = apps.get_model("poultry", "PoultryHouse")

    if not PoultryBatch.objects.filter(house__isnull=True).exists():
        return

    house_code = "UNASSIGNED"
    suffix = 1
    while PoultryHouse.objects.filter(house_code=house_code).exists():
        suffix += 1
        house_code = f"UNASSIGNED-{suffix}"

    placeholder_house = PoultryHouse.objects.create(
        house_code=house_code,
        name="Unassigned House",
        capacity=1,
        is_active=False,
    )
    PoultryBatch.objects.filter(house__isnull=True).update(house=placeholder_house)


class Migration(migrations.Migration):

    dependencies = [
        ("poultry", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PoultryHouse",
            fields=[
                ("house_id", models.BigAutoField(primary_key=True, serialize=False)),
                ("house_code", models.CharField(max_length=50, unique=True)),
                ("name", models.CharField(blank=True, max_length=100)),
                (
                    "capacity",
                    models.PositiveIntegerField(
                        help_text="Maximum number of birds this house can hold",
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.AddField(
            model_name="poultrybatch",
            name="house",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="batches",
                to="poultry.poultryhouse",
            ),
        ),
        migrations.RunPython(
            assign_placeholder_house_to_existing_batches,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="poultrybatch",
            name="house",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="batches",
                to="poultry.poultryhouse",
            ),
        ),
        migrations.AlterField(
            model_name="poultrybatch",
            name="status",
            field=models.CharField(
                choices=[("ACTIVE", "Active"), ("CLOSED", "Closed")],
                default="ACTIVE",
                max_length=10,
            ),
        ),
        migrations.AlterModelOptions(
            name="poultrybatch",
            options={},
        ),
    ]
