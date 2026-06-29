from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0003_customer_payment_profiles_invoice_method"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="pay_wht",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="saleinvoice",
            name="wht_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=14,
                validators=[django.core.validators.MinValueValidator(Decimal("0.00"))],
            ),
        ),
    ]
