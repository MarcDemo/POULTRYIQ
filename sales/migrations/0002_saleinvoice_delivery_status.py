from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="saleinvoice",
            name="delivery_status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending"),
                    ("DELIVERED", "Delivered"),
                    ("CANCELLED", "Cancelled"),
                ],
                db_index=True,
                default="PENDING",
                max_length=12,
            ),
        ),
    ]
