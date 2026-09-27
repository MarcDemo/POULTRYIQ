from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0019_chartofaccount_system_code_seed_requested_defaults"),
        ("sales", "0005_saleitem_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="saleitem",
            name="account",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sale_items",
                to="accounting.chartofaccount",
            ),
        ),
    ]
