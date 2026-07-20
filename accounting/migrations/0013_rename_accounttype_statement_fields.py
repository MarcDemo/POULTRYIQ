from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0012_alter_accounttype_options_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="accounttype",
            old_name="statement_section",
            new_name="account_nature",
        ),
        migrations.RenameField(
            model_name="accounttype",
            old_name="report_section",
            new_name="statement_section",
        ),
        migrations.AlterField(
            model_name="accounttype",
            name="account_nature",
            field=models.CharField(
                choices=[
                    ("ASSET", "Asset"),
                    ("LIABILITY", "Liability"),
                    ("EQUITY", "Equity"),
                    ("INCOME", "Income"),
                    ("EXPENSE", "Expense"),
                ],
                db_index=True,
                help_text="The accounting nature used for totals and normal balance logic.",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="accounttype",
            name="statement_section",
            field=models.ForeignKey(
                blank=True,
                help_text="The section where this account type appears, such as Profit and Loss / Income.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="account_types",
                to="accounting.financialstatementsection",
            ),
        ),
        migrations.AlterModelOptions(
            name="accounttype",
            options={
                "ordering": [
                    "statement_section__statement__display_order",
                    "statement_section__display_order",
                    "name",
                ],
            },
        ),
        migrations.AlterModelOptions(
            name="chartofaccount",
            options={
                "ordering": [
                    "account_type__statement_section__statement__display_order",
                    "account_type__statement_section__display_order",
                    "account_type__name",
                    "code",
                ],
            },
        ),
    ]
