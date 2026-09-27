from django.db import migrations, models


def backfill_supplied_accounts(apps, schema_editor):
    Supplier = apps.get_model("inventory", "Supplier")
    ChartOfAccount = apps.get_model("accounting", "ChartOfAccount")

    for supplier in Supplier.objects.exclude(product=""):
        names = [name.strip() for name in supplier.product.split(",") if name.strip()]
        for name in names:
            account = ChartOfAccount.objects.filter(account_name__iexact=name, is_active=True).first()
            if account:
                supplier.supplied_accounts.add(account)


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0019_chartofaccount_system_code_seed_requested_defaults"),
        ("inventory", "0011_rename_credit_paid_upfront_columns"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplier",
            name="supplied_accounts",
            field=models.ManyToManyField(blank=True, related_name="suppliers", to="accounting.chartofaccount"),
        ),
        migrations.RunPython(backfill_supplied_accounts, migrations.RunPython.noop),
    ]
