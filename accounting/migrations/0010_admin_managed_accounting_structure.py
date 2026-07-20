from django.db import migrations, models
import django.db.models.deletion


ACCOUNT_TYPES = [
    ("Fixed Asset", "FI", "ASSET", "FIXED_ASSET"),
    ("Accumulated Depreciation", "AC", "ASSET", "ACCUMULATED_DEPRECIATION"),
    ("Current Asset", "CU", "ASSET", "CURRENT_ASSET"),
    ("Receivable", "REC", "ASSET", "RECEIVABLE"),
    ("Prepayment", "PR", "ASSET", "PREPAYMENT"),
    ("Bank and Cash", "BA", "ASSET", "BANK_AND_CASH"),
    ("Non Current Liability", "NO", "LIABILITY", "NON_CURRENT_LIABILITY"),
    ("Current Liability", "CUR", "LIABILITY", "CURRENT_LIABILITY"),
    ("Payable", "PA", "LIABILITY", "PAYABLE"),
    ("Equity", "EQ", "EQUITY", "EQUITY"),
    ("Current Year Earnings", "CY", "EQUITY", "CURRENT_YEAR_EARNINGS"),
    ("Revenue", "RE", "INCOME", "REVENUE"),
    ("Other Income", "OT", "INCOME", "OTHER_INCOME"),
    ("Cost of Revenue", "CO", "EXPENSE", "COST_OF_REVENUE"),
    ("Depreciation", "DE", "EXPENSE", "DEPRECIATION"),
    ("Expenses", "EX", "EXPENSE", "EXPENSES"),
    ("Other Expenses", "OTH", "EXPENSE", "OTHER_EXPENSES"),
    ("Monthly Expenses", "MO", "EXPENSE", "MONTHLY_EXPENSES"),
]


ASSET_CATEGORY_MAP = {
    "BUILDING": ("Building", "311001", "312001", None, 20, False, True, True),
    "LAND": ("Land", "311001", None, None, None, True, False, False),
    "POULTRY_HOUSE": ("Poultry House", "311001", "312001", None, 20, False, True, True),
    "BROILER_HOUSE": ("Broiler House", "311001", "312001", None, 20, False, True, True),
    "LAYER_HOUSE": ("Layer House", "311001", "312001", None, 20, False, True, True),
    "HATCHERY_BUILDING": ("Hatchery Building", "311001", "312001", None, 20, False, True, True),
    "FEED_STORE": ("Feed Store / Warehouse", "311001", "312001", None, 20, False, True, True),
    "OFFICE_BUILDING": ("Office Building", "311001", "312001", None, 20, False, True, True),
    "STAFF_QUARTERS": ("Staff Quarters", "311001", "312001", None, 20, False, True, True),
    "SECURITY_BOOTH": ("Security Booth", "311001", "312001", None, 20, False, True, True),
    "GENERAL_BUILDING": ("General Building", "311001", "312001", None, 20, False, True, True),
    "CAGES_REARING": ("Cages & Rearing Equipment", "311008", "312008", None, 10, False, True, False),
    "FEEDERS": ("Feeders", "311008", "312008", None, 10, False, True, False),
    "DRINKERS_WATERING": ("Drinkers / Watering Systems", "311008", "312008", None, 10, False, True, False),
    "INCUBATORS": ("Incubators & Hatchery Equipment", "311008", "312008", None, 10, False, True, False),
    "EGG_HANDLING": ("Egg Handling Equipment", "311008", "312008", None, 10, False, True, False),
    "EGG_GRADING": ("Egg Grading & Packing Equipment", "311008", "312008", None, 10, False, True, False),
    "VENTILATION": ("Ventilation & Climate Control Systems", "311008", "312008", None, 10, False, True, False),
    "LIGHTING": ("Lighting Systems", "311008", "312008", None, 10, False, True, False),
    "BIOSECURITY": ("Biosecurity Equipment", "311008", "312008", None, 10, False, True, False),
    "WASTE_MANAGEMENT": ("Waste Management Systems", "311008", "312008", None, 10, False, True, False),
    "FEED_MIXING": ("Feed Mixing Equipment", "311008", "312008", None, 10, False, True, False),
    "FEED_STORAGE": ("Feed Storage Silos / Bins", "311008", "312008", None, 10, False, True, False),
    "TRACTOR_MACHINERY": ("Tractor & Farm Machinery", "311008", "312008", None, 10, False, True, False),
    "GENERATOR": ("Generator / Electrical Installation", "311008", "312008", None, 10, False, True, False),
    "BOREHOLE_WATER": ("Borehole & Water Supply Systems", "311008", "312008", None, 10, False, True, False),
    "COLD_STORAGE": ("Cold Storage / Refrigeration", "311008", "312008", None, 10, False, True, False),
    "VEHICLE": ("Vehicle (Delivery / Farm)", "311005", "312005", None, 5, False, True, False),
    "COMPUTER_EQUIPMENT": ("Computer Equipment", "311007", "312007", None, 4, False, True, False),
    "OFFICE_EQUIPMENT": ("Office Equipment", "311002", "312002", None, 5, False, True, False),
    "FURNITURE_FITTINGS": ("Furniture & Fittings", "311003", "312003", None, 8, False, True, False),
    "SECURITY_SYSTEM": ("Security System (CCTV / Alarm)", "311008", "312008", None, 5, False, True, False),
    "SOFTWARE": ("Software", "311006", "312006", None, 3, False, True, False),
    "HARDWARE_EQUIPMENT": ("Hardware Equipment", "311008", "312008", None, 5, False, True, False),
    "OTHER": ("Other", "311008", "312008", None, 5, False, True, False),
}


REQUIRED_CHART_ACCOUNTS = {
    "311001": ("Buildings Cost", "FIXED_ASSET"),
    "311002": ("Office Equipment Cost", "FIXED_ASSET"),
    "311003": ("Furniture & Fittings Cost", "FIXED_ASSET"),
    "311005": ("Vehicles Cost", "FIXED_ASSET"),
    "311006": ("Software Cost", "FIXED_ASSET"),
    "311007": ("Computer Equipment Cost", "FIXED_ASSET"),
    "311008": ("Farm Equipment Cost", "FIXED_ASSET"),
    "312001": ("Accumulated Depreciation - Buildings", "ACCUMULATED_DEPRECIATION"),
    "312002": ("Accumulated Depreciation - Office Equipment", "ACCUMULATED_DEPRECIATION"),
    "312003": ("Accumulated Depreciation - Furniture & Fittings", "ACCUMULATED_DEPRECIATION"),
    "312005": ("Accumulated Depreciation - Vehicles", "ACCUMULATED_DEPRECIATION"),
    "312006": ("Accumulated Amortisation - Software", "ACCUMULATED_DEPRECIATION"),
    "312007": ("Accumulated Depreciation - Computer Equipment", "ACCUMULATED_DEPRECIATION"),
    "312008": ("Accumulated Depreciation - Farm Equipment", "ACCUMULATED_DEPRECIATION"),
}


def seed_accounting_structure(apps, schema_editor):
    AccountType = apps.get_model("accounting", "AccountType")
    BalanceSheetAccount = apps.get_model("accounting", "BalanceSheetAccount")
    ChartOfAccount = apps.get_model("accounting", "ChartOfAccount")
    AssetCategory = apps.get_model("accounting", "AssetCategory")

    type_by_legacy = {}
    for name, prefix, section, legacy_code in ACCOUNT_TYPES:
        account_type, _ = AccountType.objects.update_or_create(
            legacy_code=legacy_code,
            defaults={
                "name": name,
                "prefix": prefix,
                "statement_section": section,
                "is_active": True,
            },
        )
        type_by_legacy[legacy_code] = account_type

    for old_account in BalanceSheetAccount.objects.all():
        account_type = type_by_legacy.get(old_account.account_type)
        if not account_type:
            continue
        ChartOfAccount.objects.update_or_create(
            code=old_account.code,
            defaults={
                "account_name": old_account.account_name,
                "account_type": account_type,
                "allow_reconciliation": old_account.allow_reconciliation,
                "description": old_account.description,
                "is_active": old_account.is_active,
            },
        )

    for code, (account_name, legacy_type) in REQUIRED_CHART_ACCOUNTS.items():
        account_type = type_by_legacy.get(legacy_type)
        if not account_type:
            continue
        ChartOfAccount.objects.update_or_create(
            code=code,
            defaults={
                "account_name": account_name,
                "account_type": account_type,
                "allow_reconciliation": False,
                "description": "",
                "is_active": True,
            },
        )

    chart_by_code = {account.code: account for account in ChartOfAccount.objects.all()}
    for legacy_code, config in ASSET_CATEGORY_MAP.items():
        name, asset_code, accumulated_code, expense_code, life, is_land, depreciable, construction_only = config
        asset_account = chart_by_code.get(asset_code)
        if not asset_account:
            continue
        AssetCategory.objects.update_or_create(
            legacy_code=legacy_code,
            defaults={
                "name": name,
                "asset_account": asset_account,
                "accumulated_depreciation_account": chart_by_code.get(accumulated_code),
                "depreciation_expense_account": chart_by_code.get(expense_code),
                "useful_life_years": life,
                "is_land": is_land,
                "is_depreciable": depreciable,
                "is_construction_only": construction_only,
                "is_active": True,
            },
        )


def migrate_asset_category_values(apps, schema_editor):
    AssetCategory = apps.get_model("accounting", "AssetCategory")
    FixedAssetAcquisition = apps.get_model("accounting", "FixedAssetAcquisition")
    AssetConstructionProject = apps.get_model("accounting", "AssetConstructionProject")

    categories = {category.legacy_code: category for category in AssetCategory.objects.all()}
    fallback = categories.get("OTHER") or next(iter(categories.values()), None)
    if not fallback:
        return

    for asset in FixedAssetAcquisition.objects.all():
        asset.asset_category = categories.get(asset.legacy_asset_category, fallback)
        asset.save(update_fields=["asset_category"])

    for project in AssetConstructionProject.objects.all():
        project.asset_category = categories.get(project.legacy_asset_category, fallback)
        project.save(update_fields=["asset_category"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0009_add_prepaid_water_account"),
    ]

    operations = [
        migrations.CreateModel(
            name="AccountType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("prefix", models.CharField(editable=False, help_text="Generated from the account type name and kept stable after creation.", max_length=10, unique=True)),
                ("statement_section", models.CharField(choices=[("ASSET", "Asset"), ("LIABILITY", "Liability"), ("EQUITY", "Equity"), ("INCOME", "Income"), ("EXPENSE", "Expense")], db_index=True, max_length=20)),
                ("legacy_code", models.CharField(blank=True, help_text="Optional old reporting code, used only for backwards-compatible reports.", max_length=40, null=True, unique=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["statement_section", "name"]},
        ),
        migrations.CreateModel(
            name="ChartOfAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(blank=True, db_index=True, max_length=20, unique=True)),
                ("account_name", models.CharField(max_length=150)),
                ("allow_reconciliation", models.BooleanField(default=False)),
                ("description", models.TextField(blank=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("account_type", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="accounts", to="accounting.accounttype")),
            ],
            options={"ordering": ["account_type__statement_section", "account_type__name", "code"]},
        ),
        migrations.CreateModel(
            name="AssetCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True)),
                ("useful_life_years", models.PositiveIntegerField(blank=True, null=True)),
                ("is_land", models.BooleanField(db_index=True, default=False)),
                ("is_depreciable", models.BooleanField(db_index=True, default=True)),
                ("is_construction_only", models.BooleanField(db_index=True, default=False)),
                ("legacy_code", models.CharField(blank=True, max_length=40, null=True, unique=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("accumulated_depreciation_account", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="accumulated_depreciation_categories", to="accounting.chartofaccount")),
                ("asset_account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="asset_categories", to="accounting.chartofaccount")),
                ("depreciation_expense_account", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="depreciation_expense_categories", to="accounting.chartofaccount")),
            ],
            options={"verbose_name_plural": "Asset categories", "ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="PaymentMethod",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payment_methods", to="accounting.chartofaccount")),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="TransactionCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True)),
                ("description", models.TextField(blank=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="transaction_categories", to="accounting.chartofaccount")),
            ],
            options={"verbose_name_plural": "Transaction categories", "ordering": ["name"]},
        ),
        migrations.AddIndex(
            model_name="chartofaccount",
            index=models.Index(fields=["account_type", "code"], name="accounting_c_account_24bbba_idx"),
        ),
        migrations.RunPython(seed_accounting_structure, migrations.RunPython.noop),
        migrations.AddField(
            model_name="accountingcode",
            name="account",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="transaction_references", to="accounting.chartofaccount"),
        ),
        migrations.AlterField(
            model_name="accountingcode",
            name="account_type",
            field=models.CharField(db_index=True, max_length=40),
        ),
        migrations.AlterField(
            model_name="accountingcode",
            name="prefix",
            field=models.CharField(max_length=10),
        ),
        migrations.RenameField(
            model_name="fixedassetacquisition",
            old_name="asset_category",
            new_name="legacy_asset_category",
        ),
        migrations.RenameField(
            model_name="assetconstructionproject",
            old_name="asset_category",
            new_name="legacy_asset_category",
        ),
        migrations.AddField(
            model_name="fixedassetacquisition",
            name="asset_category",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="acquisitions", to="accounting.assetcategory"),
        ),
        migrations.AddField(
            model_name="assetconstructionproject",
            name="asset_category",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="construction_projects", to="accounting.assetcategory"),
        ),
        migrations.RunPython(migrate_asset_category_values, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="fixedassetacquisition",
            name="asset_category",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="acquisitions", to="accounting.assetcategory"),
        ),
        migrations.AlterField(
            model_name="assetconstructionproject",
            name="asset_category",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="construction_projects", to="accounting.assetcategory"),
        ),
        migrations.RemoveField(
            model_name="fixedassetacquisition",
            name="legacy_asset_category",
        ),
        migrations.RemoveField(
            model_name="assetconstructionproject",
            name="legacy_asset_category",
        ),
    ]
