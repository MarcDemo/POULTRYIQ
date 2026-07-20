from django.db import migrations, models
import django.db.models.deletion


DEFAULT_STATEMENT_NATURES = {
    "BALANCE_SHEET": [
        ("ASSET", 10),
        ("LIABILITY", 20),
        ("EQUITY", 30),
    ],
    "PROFIT_LOSS": [
        ("INCOME", 10),
        ("EXPENSE", 20),
    ],
}


DEFAULT_STATEMENTS = {
    "BALANCE_SHEET": ("Balance Sheet", 10),
    "PROFIT_LOSS": ("Profit and Loss", 20),
}


def seed_statement_natures(apps, schema_editor):
    FinancialStatement = apps.get_model("accounting", "FinancialStatement")
    FinancialStatementAccountNature = apps.get_model("accounting", "FinancialStatementAccountNature")

    for statement_code, natures in DEFAULT_STATEMENT_NATURES.items():
        name, statement_order = DEFAULT_STATEMENTS[statement_code]
        statement, _ = FinancialStatement.objects.update_or_create(
            code=statement_code,
            defaults={
                "name": name,
                "display_order": statement_order,
                "is_active": True,
            },
        )
        for account_nature, display_order in natures:
            FinancialStatementAccountNature.objects.update_or_create(
                statement=statement,
                account_nature=account_nature,
                defaults={
                    "display_order": display_order,
                    "is_active": True,
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0013_rename_accounttype_statement_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="FinancialStatementAccountNature",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("account_nature", models.CharField(choices=[("ASSET", "Asset"), ("LIABILITY", "Liability"), ("EQUITY", "Equity"), ("INCOME", "Income"), ("EXPENSE", "Expense")], db_index=True, max_length=20)),
                ("display_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("statement", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="account_natures", to="accounting.financialstatement")),
            ],
            options={
                "ordering": ["statement__display_order", "display_order", "account_nature"],
                "unique_together": {("statement", "account_nature")},
            },
        ),
        migrations.AlterModelOptions(
            name="chartofaccount",
            options={"ordering": ["account_type__account_nature", "account_type__name", "code"]},
        ),
        migrations.RunPython(seed_statement_natures, migrations.RunPython.noop),
    ]
