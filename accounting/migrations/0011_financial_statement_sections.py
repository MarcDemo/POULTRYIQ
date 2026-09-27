from django.db import migrations, models
import django.db.models.deletion


STATEMENTS = [
    ("BALANCE_SHEET", "Balance Sheet", 10),
    ("PROFIT_LOSS", "Profit and Loss", 20),
]


SECTIONS = [
    ("BALANCE_SHEET", "ASSETS", "Assets", 10),
    ("BALANCE_SHEET", "LIABILITIES", "Liabilities", 20),
    ("BALANCE_SHEET", "EQUITY", "Equity", 30),
    ("PROFIT_LOSS", "REVENUE", "Revenue", 10),
    ("PROFIT_LOSS", "OTHER_INCOME", "Other Income", 20),
    ("PROFIT_LOSS", "COST_OF_REVENUE", "Cost of Revenue", 30),
    ("PROFIT_LOSS", "DEPRECIATION", "Depreciation", 40),
    ("PROFIT_LOSS", "EXPENSES", "Expenses", 50),
    ("PROFIT_LOSS", "OTHER_EXPENSES", "Other Expenses", 60),
]


ACCOUNT_TYPE_SECTIONS = {
    "FIXED_ASSET": ("BALANCE_SHEET", "ASSETS"),
    "ACCUMULATED_DEPRECIATION": ("BALANCE_SHEET", "ASSETS"),
    "CURRENT_ASSET": ("BALANCE_SHEET", "ASSETS"),
    "RECEIVABLE": ("BALANCE_SHEET", "ASSETS"),
    "PREPAYMENT": ("BALANCE_SHEET", "ASSETS"),
    "BANK_AND_CASH": ("BALANCE_SHEET", "ASSETS"),
    "NON_CURRENT_LIABILITY": ("BALANCE_SHEET", "LIABILITIES"),
    "CURRENT_LIABILITY": ("BALANCE_SHEET", "LIABILITIES"),
    "PAYABLE": ("BALANCE_SHEET", "LIABILITIES"),
    "EQUITY": ("BALANCE_SHEET", "EQUITY"),
    "CURRENT_YEAR_EARNINGS": ("BALANCE_SHEET", "EQUITY"),
    "REVENUE": ("PROFIT_LOSS", "REVENUE"),
    "OTHER_INCOME": ("PROFIT_LOSS", "OTHER_INCOME"),
    "COST_OF_REVENUE": ("PROFIT_LOSS", "COST_OF_REVENUE"),
    "DEPRECIATION": ("PROFIT_LOSS", "DEPRECIATION"),
    "EXPENSES": ("PROFIT_LOSS", "EXPENSES"),
    "MONTHLY_EXPENSES": ("PROFIT_LOSS", "EXPENSES"),
    "OTHER_EXPENSES": ("PROFIT_LOSS", "OTHER_EXPENSES"),
}


def seed_financial_statements(apps, schema_editor):
    FinancialStatement = apps.get_model("accounting", "FinancialStatement")
    FinancialStatementSection = apps.get_model("accounting", "FinancialStatementSection")
    AccountType = apps.get_model("accounting", "AccountType")

    statements = {}
    for code, name, display_order in STATEMENTS:
        statement, _ = FinancialStatement.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "display_order": display_order,
                "is_active": True,
            },
        )
        statements[code] = statement

    sections = {}
    for statement_code, section_code, name, display_order in SECTIONS:
        section, _ = FinancialStatementSection.objects.update_or_create(
            statement=statements[statement_code],
            code=section_code,
            defaults={
                "name": name,
                "display_order": display_order,
                "is_active": True,
            },
        )
        sections[(statement_code, section_code)] = section

    for account_type in AccountType.objects.all():
        key = ACCOUNT_TYPE_SECTIONS.get(account_type.legacy_code)
        if not key:
            continue
        account_type.report_section = sections[key]
        account_type.save(update_fields=["report_section"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0010_admin_managed_accounting_structure"),
    ]

    operations = [
        migrations.CreateModel(
            name="FinancialStatement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("code", models.CharField(db_index=True, max_length=30, unique=True)),
                ("display_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["display_order", "name"]},
        ),
        migrations.CreateModel(
            name="FinancialStatementSection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("code", models.CharField(db_index=True, max_length=30)),
                ("display_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("statement", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sections", to="accounting.financialstatement")),
            ],
            options={
                "ordering": ["statement__display_order", "display_order", "name"],
                "unique_together": {("statement", "code")},
            },
        ),
        migrations.AddField(
            model_name="accounttype",
            name="report_section",
            field=models.ForeignKey(
                blank=True,
                help_text="Controls which financial statement/report this account type appears in.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="account_types",
                to="accounting.financialstatementsection",
            ),
        ),
        migrations.RunPython(seed_financial_statements, migrations.RunPython.noop),
    ]
