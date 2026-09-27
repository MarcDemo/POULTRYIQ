from django.db import migrations, models


DEFAULT_SUPPLIER_TYPES = {"EXPENSES", "COST_OF_REVENUE"}


def enable_current_supplier_accounts(apps, schema_editor):
    AccountType = apps.get_model("accounting", "AccountType")
    ChartOfAccount = apps.get_model("accounting", "ChartOfAccount")

    account_types = AccountType.objects.filter(legacy_code__in=DEFAULT_SUPPLIER_TYPES)
    account_types.update(show_on_suppliers=True)
    ChartOfAccount.objects.filter(account_type__in=account_types).update(show_on_suppliers=True)


def disable_current_supplier_accounts(apps, schema_editor):
    AccountType = apps.get_model("accounting", "AccountType")
    ChartOfAccount = apps.get_model("accounting", "ChartOfAccount")

    account_types = AccountType.objects.filter(legacy_code__in=DEFAULT_SUPPLIER_TYPES)
    ChartOfAccount.objects.filter(account_type__in=account_types).update(show_on_suppliers=False)
    account_types.update(show_on_suppliers=False)


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0020_seed_builtin_payment_accounts"),
    ]

    operations = [
        migrations.AddField(
            model_name="accounttype",
            name="show_on_suppliers",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text="Allow chart accounts under this type to be selected on the suppliers page.",
            ),
        ),
        migrations.AddField(
            model_name="chartofaccount",
            name="show_on_suppliers",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text="Allow this chart account to be selected for supplier products.",
            ),
        ),
        migrations.RunPython(enable_current_supplier_accounts, disable_current_supplier_accounts),
    ]
