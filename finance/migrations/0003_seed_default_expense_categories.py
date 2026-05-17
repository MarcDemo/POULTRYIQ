from django.db import migrations


def seed_default_expense_categories(apps, schema_editor):
    ExpenseCategory = apps.get_model("finance", "ExpenseCategory")

    default_categories = [
        ("FEED", "Feed & Nutrition"),
        ("VET", "Veterinary & Medication"),
        ("LABOUR", "Labour & Wages"),
        ("UTILITIES", "Utilities"),
        ("TRANSPORT", "Transport & Logistics"),
        ("MAINTENANCE", "Maintenance & Repairs"),
        ("BEDDING", "Bedding & Litter"),
        ("EQUIPMENT", "Equipment & Tools"),
        ("OTHER", "Other"),
    ]

    for code, name in default_categories:
        ExpenseCategory.objects.get_or_create(
            code=code,
            defaults={"name": name, "is_active": True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0002_expensetransaction_payment_method_salarypayment"),
    ]

    operations = [
        migrations.RunPython(seed_default_expense_categories, migrations.RunPython.noop),
    ]
