from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0010_admin_managed_accounting_structure"),
        ("sales", "0004_customer_pay_wht_saleinvoice_wht_amount"),
    ]

    operations = [
        migrations.AddField(
            model_name="saleitem",
            name="category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sales",
                to="accounting.transactioncategory",
            ),
        ),
    ]
