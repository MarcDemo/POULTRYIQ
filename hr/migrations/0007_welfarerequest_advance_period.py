from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hr", "0006_staffprofile_payroll_flags"),
    ]

    operations = [
        migrations.AddField(
            model_name="welfarerequest",
            name="advance_period_start",
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="welfarerequest",
            name="advance_period_end",
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
    ]
