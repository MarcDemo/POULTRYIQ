from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0004_inventorytransaction_supplier_price_expiry"),
        ("poultry", "0012_rename_poultry_fee_mix_dat_e17981_idx_poultry_fee_mix_dat_d489a7_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="feedmixture",
            name="total_weight_kg",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=10,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0.00"))],
            ),
        ),
        migrations.AddField(
            model_name="feedmixtureingredient",
            name="item",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="feed_mixture_ingredients",
                to="inventory.item",
            ),
        ),
    ]
