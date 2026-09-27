from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0019_chartofaccount_system_code_seed_requested_defaults"),
        ("expenses", "0004_expensecategory_account"),
    ]

    operations = [
        migrations.AddField(
            model_name="expensetransaction",
            name="account",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="expense_transactions",
                to="accounting.chartofaccount",
            ),
        ),
    ]
