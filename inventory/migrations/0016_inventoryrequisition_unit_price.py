from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0015_unique_supplier_product_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventoryrequisition",
            name="unit_price",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=14,
                null=True,
                validators=[MinValueValidator(Decimal("0.00"))],
            ),
        ),
    ]
