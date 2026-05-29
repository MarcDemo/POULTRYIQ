from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0004_inventorytransaction_supplier_price_expiry"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplier",
            name="tin_number",
            field=models.CharField(blank=True, max_length=50),
        ),
    ]
