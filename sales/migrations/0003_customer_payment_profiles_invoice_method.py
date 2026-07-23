from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0002_saleinvoice_delivery_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="bank_account_number",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="customer",
            name="credit_repayment_plan",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="customer",
            name="momo_receiving_number",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="customer",
            name="preferred_payment_method",
            field=models.CharField(
                choices=[
                    ("CASH", "Cash"),
                    ("MOMO", "Mobile Money"),
                    ("BANK", "Bank"),
                    ("CREDIT", "Credit"),
                ],
                default="CASH",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="saleinvoice",
            name="payment_method",
            field=models.CharField(
                choices=[
                    ("CASH", "Cash"),
                    ("MOMO", "Mobile Money"),
                    ("BANK", "Bank"),
                    ("CREDIT", "Credit"),
                ],
                default="CASH",
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="customerpayment",
            name="method",
            field=models.CharField(
                choices=[
                    ("CASH", "Cash"),
                    ("MOMO", "Mobile Money"),
                    ("BANK", "Bank"),
                    ("CREDIT", "Credit"),
                    ("OTHER", "Other"),
                ],
                default="CASH",
                max_length=10,
            ),
        ),
    ]
