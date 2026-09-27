from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0013_supplierproduct_supplier_supplied_products"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplier",
            name="other_supplied_products",
            field=models.TextField(blank=True),
        ),
    ]
