from django.db import migrations, models

from accounting.balance_sheet_data import BALANCE_SHEET_ACCOUNTS


def seed_balance_sheet_accounts(apps, schema_editor):
    BalanceSheetAccount = apps.get_model("accounting", "BalanceSheetAccount")
    for row in BALANCE_SHEET_ACCOUNTS:
        code, account_name, group, account_type, allow_reconciliation, *rest = row
        description = rest[0] if rest else ""
        BalanceSheetAccount.objects.update_or_create(
            code=code,
            defaults={
                "account_name": account_name,
                "group": group,
                "account_type": account_type,
                "allow_reconciliation": allow_reconciliation,
                "description": description,
                "is_active": True,
            },
        )


def unseed_balance_sheet_accounts(apps, schema_editor):
    BalanceSheetAccount = apps.get_model("accounting", "BalanceSheetAccount")
    BalanceSheetAccount.objects.filter(code__in=[row[0] for row in BALANCE_SHEET_ACCOUNTS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="BalanceSheetAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(db_index=True, max_length=20, unique=True)),
                ("account_name", models.CharField(max_length=150)),
                (
                    "group",
                    models.CharField(
                        choices=[("ASSETS", "Assets"), ("LIABILITIES", "Liabilities"), ("EQUITY", "Equity")],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                (
                    "account_type",
                    models.CharField(
                        choices=[
                            ("FIXED_ASSET", "Fixed Asset"),
                            ("CURRENT_ASSET", "Current Asset"),
                            ("RECEIVABLE", "Receivable"),
                            ("PREPAYMENT", "Prepayment"),
                            ("BANK_AND_CASH", "Bank and Cash"),
                            ("NON_CURRENT_LIABILITY", "Non Current Liability"),
                            ("CURRENT_LIABILITY", "Current Liability"),
                            ("PAYABLE", "Payable"),
                            ("EQUITY", "Equity"),
                            ("CURRENT_YEAR_EARNINGS", "Current Year Earnings"),
                        ],
                        db_index=True,
                        max_length=30,
                    ),
                ),
                ("allow_reconciliation", models.BooleanField(default=False)),
                ("description", models.TextField(blank=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["group", "account_type", "code"],
            },
        ),
        migrations.AddIndex(
            model_name="balancesheetaccount",
            index=models.Index(fields=["group", "account_type"], name="accounting__group_41aa4c_idx"),
        ),
        migrations.RunPython(seed_balance_sheet_accounts, unseed_balance_sheet_accounts),
    ]
