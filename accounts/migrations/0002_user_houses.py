from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("poultry", "0002_poultryhouse_poultrybatch_house"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="houses",
            field=models.ManyToManyField(
                blank=True,
                related_name="assigned_users",
                to="poultry.poultryhouse",
            ),
        ),
    ]
