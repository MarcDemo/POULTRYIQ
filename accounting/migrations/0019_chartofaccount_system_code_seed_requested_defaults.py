from django.db import migrations, models


ACCOUNT_TYPES = {
    "REVENUE": ("Revenue", "REV", "INCOME", ["Income", "Revenue"]),
    "CURRENT_LIABILITY": ("Current Liability", "CL", "LIABILITY", ["current liabilities", "Current Liability"]),
    "CURRENT_ASSET": ("Current Asset", "CA", "ASSET", ["currents assets", "Current Asset"]),
    "EXPENSES": ("Expenses", "EX2", "EXPENSE", ["expenses", "Expenses"]),
    "COST_OF_REVENUE": ("Cost of Revenue", "COR", "EXPENSE", ["cost of revenue", "Cost of Revenue"]),
}


DEFAULT_ACCOUNTS = [
    ("SALE_EGGS", "Sale of Eggs", "REVENUE"),
    ("SALE_MANURE", "Sale of Manure", "REVENUE"),
    ("SALE_OFFLAYERS", "Sale of Off-Layers", "REVENUE"),
    ("SALE_DAMAGED_EGGS", "Sale of Damaged Eggs", "REVENUE"),
    ("PREPAID_ORDERS", "Prepaid Orders", "CURRENT_LIABILITY"),
    ("CREDIT_ORDERS", "Credit Orders", "CURRENT_ASSET"),
    ("SALARIES_EXPENSE", "Salaries", "EXPENSES"),
    ("BONUSES_EXPENSE", "Bonuses", "EXPENSES"),
    ("EMPLOYER_NSSF_EXPENSE", "Employer NSSF", "EXPENSES"),
    ("VACCINATIONS_COST", "Vaccinations", "COST_OF_REVENUE"),
    ("TREATMENTS_COST", "Treatments", "COST_OF_REVENUE"),
    ("FIXED_ASSET_CONSTRUCTION", "Fixed Asset Construction", "EXPENSES"),
]


def seed_requested_defaults(apps, schema_editor):
    AccountType = apps.get_model("accounting", "AccountType")
    ChartOfAccount = apps.get_model("accounting", "ChartOfAccount")

    type_by_legacy = {}
    for legacy_code, (name, prefix, nature, aliases) in ACCOUNT_TYPES.items():
        account_type = AccountType.objects.filter(legacy_code=legacy_code).first()
        if not account_type:
            account_type = AccountType.objects.filter(name__in=aliases).first()
        if account_type:
            updates = []
            if account_type.legacy_code != legacy_code:
                account_type.legacy_code = legacy_code
                updates.append("legacy_code")
            if account_type.account_nature != nature:
                account_type.account_nature = nature
                updates.append("account_nature")
            if not account_type.is_active:
                account_type.is_active = True
                updates.append("is_active")
            if updates:
                account_type.save(update_fields=updates)
        else:
            account_type = AccountType.objects.create(
                legacy_code=legacy_code,
                name=name,
                prefix=prefix,
                account_nature=nature,
                is_active=True,
            )
        type_by_legacy[legacy_code] = account_type

    for system_code, account_name, legacy_code in DEFAULT_ACCOUNTS:
        account_type = type_by_legacy[legacy_code]
        account = ChartOfAccount.objects.filter(system_code=system_code).first()
        if not account:
            prefix = account_type.prefix
            last_account = ChartOfAccount.objects.filter(code__startswith=prefix).order_by("-code").first()
            next_sequence = 1
            if last_account:
                suffix = last_account.code.replace(prefix, "", 1)
                if suffix.isdigit():
                    next_sequence = int(suffix) + 1
            code = f"{prefix}{next_sequence:04d}"
            while ChartOfAccount.objects.filter(code=code).exists():
                next_sequence += 1
                code = f"{prefix}{next_sequence:04d}"
            ChartOfAccount.objects.create(
                system_code=system_code,
                code=code,
                account_name=account_name,
                account_type=account_type,
                is_active=True,
            )


def unseed_requested_defaults(apps, schema_editor):
    ChartOfAccount = apps.get_model("accounting", "ChartOfAccount")
    ChartOfAccount.objects.filter(system_code__in=[row[0] for row in DEFAULT_ACCOUNTS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0018_default_trial_balance_cash_flow"),
    ]

    operations = [
        migrations.AddField(
            model_name="chartofaccount",
            name="system_code",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Stable internal code for built-in accounts. Admins may rename the account without breaking posting.",
                max_length=80,
                null=True,
                unique=True,
            ),
        ),
        migrations.RunPython(seed_requested_defaults, unseed_requested_defaults),
    ]
