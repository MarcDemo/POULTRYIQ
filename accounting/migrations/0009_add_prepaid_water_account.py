from django.db import migrations


def add_prepaid_water_account(apps, schema_editor):
    BalanceSheetAccount = apps.get_model("accounting", "BalanceSheetAccount")
    BalanceSheetAccount.objects.update_or_create(
        code="341004",
        defaults={
            "account_name": "Prepaid Water",
            "group": "ASSETS",
            "account_type": "PREPAYMENT",
            "allow_reconciliation": True,
            "description": "NEW",
            "is_active": True,
        },
    )


def remove_prepaid_water_account(apps, schema_editor):
    BalanceSheetAccount = apps.get_model("accounting", "BalanceSheetAccount")
    BalanceSheetAccount.objects.filter(code="341004").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0008_rename_inventory_purchases_balance_sheet_account"),
    ]

    operations = [
        migrations.RunPython(add_prepaid_water_account, remove_prepaid_water_account),
    ]
