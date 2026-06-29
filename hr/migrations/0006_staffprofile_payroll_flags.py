from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hr", "0005_staffprofile_welfare_salary_payment"),
    ]

    operations = [
        migrations.AddField(
            model_name="staffprofile",
            name="pay_nssf",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="staffprofile",
            name="pay_paye",
            field=models.BooleanField(default=True),
        ),
    ]
