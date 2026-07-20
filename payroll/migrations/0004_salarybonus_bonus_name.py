from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payroll", "0003_salary_bonus_and_edit_reason"),
    ]

    operations = [
        migrations.AddField(
            model_name="salarybonus",
            name="bonus_name",
            field=models.CharField(default="Bonus", max_length=120),
        ),
        migrations.AlterField(
            model_name="salarybonus",
            name="reason",
            field=models.TextField(blank=True),
        ),
    ]
