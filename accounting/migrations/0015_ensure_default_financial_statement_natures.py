from django.db import migrations


DEFAULT_STATEMENTS = {
    "BALANCE_SHEET": ("Balance Sheet", 10, [("ASSET", 10), ("LIABILITY", 20), ("EQUITY", 30)]),
    "PROFIT_LOSS": ("Profit and Loss", 20, [("INCOME", 10), ("EXPENSE", 20)]),
}


def ensure_default_statement_natures(apps, schema_editor):
    FinancialStatement = apps.get_model("accounting", "FinancialStatement")
    FinancialStatementAccountNature = apps.get_model("accounting", "FinancialStatementAccountNature")

    for code, (name, statement_order, natures) in DEFAULT_STATEMENTS.items():
        statement, _ = FinancialStatement.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "display_order": statement_order,
                "is_active": True,
            },
        )
        for account_nature, nature_order in natures:
            FinancialStatementAccountNature.objects.update_or_create(
                statement=statement,
                account_nature=account_nature,
                defaults={
                    "display_order": nature_order,
                    "is_active": True,
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0014_financial_statement_account_natures"),
    ]

    operations = [
        migrations.RunPython(ensure_default_statement_natures, migrations.RunPython.noop),
    ]
