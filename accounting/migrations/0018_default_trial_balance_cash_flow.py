from django.db import migrations


DEFAULT_STATEMENTS = {
    "TRIAL_BALANCE": (
        "Trial Balance",
        30,
        [("ASSET", 10), ("LIABILITY", 20), ("EQUITY", 30), ("INCOME", 40), ("EXPENSE", 50)],
    ),
    "CASH_FLOW": (
        "Cash Flow",
        40,
        [("ASSET", 10)],
    ),
}


def seed_default_statements(apps, schema_editor):
    FinancialStatement = apps.get_model("accounting", "FinancialStatement")
    FinancialStatementAccountNature = apps.get_model("accounting", "FinancialStatementAccountNature")

    for code, (name, display_order, natures) in DEFAULT_STATEMENTS.items():
        statement, _ = FinancialStatement.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "display_order": display_order,
                "is_active": True,
            },
        )
        for account_nature, nature_order in natures:
            FinancialStatementAccountNature.objects.update_or_create(
                statement=statement,
                account_nature=account_nature,
                defaults={"display_order": nature_order, "is_active": True},
            )


def unseed_default_statements(apps, schema_editor):
    FinancialStatement = apps.get_model("accounting", "FinancialStatement")
    FinancialStatement.objects.filter(code__in=DEFAULT_STATEMENTS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0017_journalentry_journalline"),
    ]

    operations = [
        migrations.RunPython(seed_default_statements, unseed_default_statements),
    ]
