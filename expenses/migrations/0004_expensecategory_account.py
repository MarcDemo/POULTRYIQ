from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0010_admin_managed_accounting_structure"),
        ("expenses", "0003_expensetransaction_prepayment_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="expensecategory",
            name="account",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="expense_categories",
                to="accounting.chartofaccount",
            ),
        ),
    ]
