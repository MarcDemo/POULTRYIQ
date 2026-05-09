from django.db import migrations


def seed_sales_categories(apps, schema_editor):
    ItemCategory = apps.get_model("inventory", "ItemCategory")
    ItemCategory.objects.get_or_create(code="MANURE", defaults={"name": "Manure"})
    ItemCategory.objects.get_or_create(code="OFF_LAYER", defaults={"name": "Off Layer Birds"})


def unseed_sales_categories(apps, schema_editor):
    ItemCategory = apps.get_model("inventory", "ItemCategory")
    ItemCategory.objects.filter(code__in=["MANURE", "OFF_LAYER"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_sales_categories, unseed_sales_categories),
    ]
