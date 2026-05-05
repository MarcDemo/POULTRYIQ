from django.db import migrations, models


def normalize_sickbay_action(apps, schema_editor):
    SicknessReport = apps.get_model("health", "SicknessReport")

    for report in SicknessReport.objects.all().iterator():
        if report.action == "isolate":
            report.action = "sickbay"

        if report.action == "sickbay":
            house_name = (report.house or "").strip() or "House"
            disease_name = (report.disease or "").strip() or "General"
            report.isolated = True
            report.isolation_name = f"{house_name}-Isolation({disease_name})"
        else:
            report.isolated = False
            report.isolation_name = ""

        report.save(update_fields=["action", "isolated", "isolation_name"])


class Migration(migrations.Migration):

    dependencies = [
        ("health", "0005_sicknessreport_bird_identifier_and_more"),
    ]

    operations = [
        migrations.RunPython(normalize_sickbay_action, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="sicknessreport",
            name="action",
            field=models.CharField(
                choices=[("sickbay", "Moved to Sickbay"), ("crowd", "Left in Crowd")],
                max_length=20,
            ),
        ),
    ]
