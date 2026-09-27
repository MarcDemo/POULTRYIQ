from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("expenses", "0002_add_credit_payment_method"),
    ]

    operations = [
        migrations.AddField(
            model_name="expensetransaction",
            name="is_prepayment",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="expensetransaction",
            name="prepayment_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("ELECTRICITY", "Prepaid Electricity"),
                    ("RENT", "Prepaid Rent"),
                    ("WATER", "Prepaid Water"),
                ],
                db_index=True,
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="expensetransaction",
            name="prepayment_start_date",
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="expensetransaction",
            name="prepayment_end_date",
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
    ]
