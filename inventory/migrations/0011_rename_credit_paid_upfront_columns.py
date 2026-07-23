from django.db import migrations


def rename_credit_paid_upfront_columns(apps, schema_editor):
    table_column_pairs = [
        ("inventory_supplier", "credit_payment_percentage", "credit_paid_upfront"),
        ("inventory_inventorytransaction", "credit_payment_percentage", "credit_paid_upfront"),
    ]

    with schema_editor.connection.cursor() as cursor:
        for table_name, old_column, new_column in table_column_pairs:
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = {row[1] for row in cursor.fetchall()}
            if old_column in columns and new_column not in columns:
                schema_editor.execute(
                    f"ALTER TABLE {table_name} RENAME COLUMN {old_column} TO {new_column}"
                )


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0010_credit_payment_schedule_fields"),
    ]

    operations = [
        migrations.RunPython(rename_credit_paid_upfront_columns, migrations.RunPython.noop),
    ]
