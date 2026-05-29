from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("poultry", "0009_poultrybatch_amount_paid"),
    ]

    operations = [
        migrations.AddField(
            model_name="egg_collection",
            name="average_egg_weight_g",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=6,
                null=True,
                validators=[MinValueValidator(Decimal("0.00"))],
            ),
        ),
    ]
