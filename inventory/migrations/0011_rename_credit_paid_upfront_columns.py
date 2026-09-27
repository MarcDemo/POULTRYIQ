from django.db import migrations


def rename_credit_paid_upfront_columns(apps, schema_editor):
    table_column_pairs = [
        ("inventory_supplier", "credit_payment_percentage", "credit_paid_upfront"),
        ("inventory_inventorytransaction", "credit_payment_percentage", "credit_paid_upfront"),
    ]

    connection = schema_editor.connection
    quote_name = schema_editor.quote_name

    with connection.cursor() as cursor:
        for table_name, old_column, new_column in table_column_pairs:
            description = connection.introspection.get_table_description(cursor, table_name)
            columns = {column.name for column in description}
            if old_column in columns and new_column not in columns:
                schema_editor.execute(
                    f"ALTER TABLE {quote_name(table_name)} "
                    f"RENAME COLUMN {quote_name(old_column)} TO {quote_name(new_column)}"
                )


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0010_credit_payment_schedule_fields"),
    ]

    operations = [
        migrations.RunPython(rename_credit_paid_upfront_columns, migrations.RunPython.noop),
    ]
