from django.db import migrations


def rename_inventory_purchases_account(apps, schema_editor):
    BalanceSheetAccount = apps.get_model("accounting", "BalanceSheetAccount")
    BalanceSheetAccount.objects.filter(code="321001").update(account_name="Inventory Purchases")


def restore_inventory_purchases_account_name(apps, schema_editor):
    BalanceSheetAccount = apps.get_model("accounting", "BalanceSheetAccount")
    BalanceSheetAccount.objects.filter(code="321001").update(account_name="Inventories (Day Old Chicks)")


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0007_alter_assetconstructionproject_asset_category_and_more"),
    ]

    operations = [
        migrations.RunPython(
            rename_inventory_purchases_account,
            restore_inventory_purchases_account_name,
        ),
    ]
