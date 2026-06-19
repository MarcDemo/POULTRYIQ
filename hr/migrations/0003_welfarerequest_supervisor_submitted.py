from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hr", "0002_welfarerequest"),
    ]

    operations = [
        migrations.AlterField(
            model_name="welfarerequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("SUBMITTED", "Submitted to Supervisor"),
                    ("SUPERVISOR_SUBMITTED", "Submitted to Manager"),
                    ("SUPERVISOR_APPROVED", "Sent to Manager"),
                    ("SUPERVISOR_REJECTED", "Rejected by Supervisor"),
                    ("MANAGER_APPROVED", "Approved by Manager"),
                    ("MANAGER_REJECTED", "Rejected by Manager"),
                ],
                db_index=True,
                default="SUBMITTED",
                max_length=24,
            ),
        ),
    ]
