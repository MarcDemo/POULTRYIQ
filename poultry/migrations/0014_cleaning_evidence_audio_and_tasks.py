from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("poultry", "0013_feedmixture_total_weight_and_ingredient_item"),
    ]

    operations = [
        migrations.AddField(
            model_name="cleaningrecord",
            name="house_dusted",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="cleaningrecord",
            name="house_raked",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="cleaningrecord",
            name="nipples_washed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="cleaningrecord",
            name="notes_audio",
            field=models.FileField(blank=True, null=True, upload_to="cleaning_audio/%Y/%m/%d/"),
        ),
        migrations.AddField(
            model_name="cleaningphoto",
            name="task_key",
            field=models.CharField(blank=True, max_length=32),
        ),
    ]
