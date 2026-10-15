from django.db import migrations


# Product names from the feed leaflet and vaccination schedule. Ages, mixing
# quantities and administration routes are reference information, not products.
PRODUCTS = {
    "FEEDS_COST": [
        ("Layer concentrate 5%", "kg", ["layer conc 5%"]),
        ("Layer concentrate 10%", "kg", ["layer 10%"]),
        ("Layer concentrate 20%", "kg", ["layer conc 20%"]),
        ("Stock feed lime", "kg", ["stock feed lime", "limestone", "lime"]),
        ("Maize", "kg", []),
        ("Broken maize", "kg", []),
        ("Maize bran", "kg", []),
        ("Soybean meal", "kg", ["soybean meals", "soya meal"]),
        ("Soybean", "kg", ["soya bean", "soyabean"]),
        ("Sunflower meal", "kg", ["sunflower meal"]),
        ("Sunflower", "kg", []),
    ],
    "VACCINATIONS_COST": [
        ("NCD/IB", "dose", []),
        ("Gumboro 1", "dose", []),
        ("Gumboro 2", "dose", []),
        ("NCD plain", "dose", []),
        ("Fowl pox vaccine", "dose", ["fowl pox"]),
        ("Fowl typhoid vaccine", "dose", ["fowl typhoid"]),
        ("Infectious coryza vaccine", "dose", ["infection coryza", "infectious coryza"]),
    ],
    "TREATMENTS_COST": [("Dewormer", "bottle", ["dewormers"])],
}

ACCOUNTS = [
    ("FEEDS_COST", "Feeds", ["feeds", "feed"]),
    ("VACCINATIONS_COST", "Vaccinations", ["vaccinations"]),
    ("TREATMENTS_COST", "Treatments", ["treatments"]),
]


def seed_products(apps, schema_editor):
    database = schema_editor.connection.alias
    AccountType = apps.get_model("accounting", "AccountType")
    ChartOfAccount = apps.get_model("accounting", "ChartOfAccount")
    SupplierProduct = apps.get_model("inventory", "SupplierProduct")
    types = AccountType.objects.using(database)
    accounts = ChartOfAccount.objects.using(database)
    products = SupplierProduct.objects.using(database)
    cost_type = types.get(legacy_code="COST_OF_REVENUE")
    types.filter(pk=cost_type.pk).update(is_active=True, show_on_suppliers=True)

    for system_code, name, aliases in ACCOUNTS:
        account = accounts.filter(system_code=system_code).first()
        if account is None:
            for alias in aliases:
                account = accounts.filter(account_type=cost_type, account_name__iexact=alias).first()
                if account is not None:
                    break
        if account is None:
            sequence = 1
            while accounts.filter(code=f"{cost_type.prefix}{sequence:04d}").exists():
                sequence += 1
            account = accounts.create(
                code=f"{cost_type.prefix}{sequence:04d}",
                account_name=name,
                account_type=cost_type,
                system_code=system_code,
                is_active=True,
                show_on_suppliers=True,
            )
        else:
            accounts.filter(pk=account.pk).update(
                system_code=system_code, account_type=cost_type,
                is_active=True, show_on_suppliers=True,
            )

        for product_name, unit, product_aliases in PRODUCTS[system_code]:
            matches = products.none()
            for candidate in [product_name, *product_aliases]:
                matches = matches | products.filter(name__iexact=candidate)
            if matches.exists():
                # Keep IDs and names so existing supplier selections remain valid.
                # Also correct products previously assigned to the wrong account.
                matches.update(account=account, unit=unit, is_active=True)
            else:
                products.create(name=product_name, account=account, unit=unit, is_active=True)


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0023_accounting_workflows"),
        ("inventory", "0016_inventoryrequisition_unit_price"),
    ]

    operations = [migrations.RunPython(seed_products, migrations.RunPython.noop)]
