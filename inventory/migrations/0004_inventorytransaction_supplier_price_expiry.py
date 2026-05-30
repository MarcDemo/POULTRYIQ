from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0003_merge_0002_seed_sales_item_categories_0002_supplier"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventorytransaction",
            name="expiry_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="inventorytransaction",
            name="supplier_name",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="inventorytransaction",
            name="unit_price",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=14,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0.00"))],
            ),
        ),
    ]
