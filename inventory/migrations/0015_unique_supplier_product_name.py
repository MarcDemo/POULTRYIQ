from django.db import migrations, models
import django.db.models.functions.text


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0014_supplier_other_supplied_products"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="supplierproduct",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower("name"),
                name="unique_supplier_product_name_ci",
            ),
        ),
    ]
