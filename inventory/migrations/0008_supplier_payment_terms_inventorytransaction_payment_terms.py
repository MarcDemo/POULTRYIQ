from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0007_rename_inventory_r_request_87a7a3_idx_inventory_i_request_fdd012_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplier",
            name="preferred_payment_method",
            field=models.CharField(
                choices=[
                    ("Cash", "Cash"),
                    ("Mobile Money", "MoMo"),
                    ("Bank", "Bank"),
                    ("Credit", "Credit"),
                ],
                default="Cash",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="supplier",
            name="credit_repayment_plan",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="supplier",
            name="credit_period",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="inventorytransaction",
            name="payment_method",
            field=models.CharField(
                choices=[
                    ("Cash", "Cash"),
                    ("Mobile Money", "MoMo"),
                    ("Bank", "Bank"),
                    ("Credit", "Credit"),
                ],
                default="Cash",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="inventorytransaction",
            name="credit_repayment_plan",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="inventorytransaction",
            name="credit_period",
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
