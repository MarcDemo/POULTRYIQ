from django.db import migrations, models
import django.db.models.deletion


def seed_products_from_supplier_accounts(apps, schema_editor):
    Supplier = apps.get_model("inventory", "Supplier")
    SupplierProduct = apps.get_model("inventory", "SupplierProduct")

    for supplier in Supplier.objects.prefetch_related("supplied_accounts"):
        product_rows = []
        for account in supplier.supplied_accounts.all():
            product, _ = SupplierProduct.objects.get_or_create(
                account=account,
                name=account.account_name,
                defaults={"unit": "unit", "is_active": True},
            )
            product_rows.append(product)
        if product_rows:
            supplier.supplied_products.add(*product_rows)


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0012_supplier_supplied_accounts"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupplierProduct",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("unit", models.CharField(default="unit", max_length=20)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "account",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="supplier_products",
                        to="accounting.chartofaccount",
                    ),
                ),
            ],
            options={
                "ordering": ["account__account_name", "name"],
                "unique_together": {("account", "name")},
            },
        ),
        migrations.AddField(
            model_name="supplier",
            name="supplied_products",
            field=models.ManyToManyField(blank=True, related_name="suppliers", to="inventory.supplierproduct"),
        ),
        migrations.RunPython(seed_products_from_supplier_accounts, migrations.RunPython.noop),
    ]
