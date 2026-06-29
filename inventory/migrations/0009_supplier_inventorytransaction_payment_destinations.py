from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0008_supplier_payment_terms_inventorytransaction_payment_terms"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplier",
            name="bank_account_number",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="supplier",
            name="momo_receiving_number",
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name="inventorytransaction",
            name="bank_account_number",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="inventorytransaction",
            name="momo_receiving_number",
            field=models.CharField(blank=True, max_length=30),
        ),
    ]
