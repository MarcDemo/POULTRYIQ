from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("expenses", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="expensetransaction",
            name="payment_method",
            field=models.CharField(
                blank=True,
                choices=[
                    ("Cash", "Cash"),
                    ("Mobile Money", "Mobile Money"),
                    ("Bank", "Bank"),
                    ("Credit", "Credit"),
                    ("Check", "Check"),
                ],
                default="Cash",
                max_length=30,
            ),
        ),
    ]
