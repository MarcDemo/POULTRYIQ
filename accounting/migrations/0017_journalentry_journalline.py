from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0016_remove_accounttype_statement_section"),
        ("contenttypes", "0002_remove_content_type_name"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="JournalEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("entry_date", models.DateField(db_index=True)),
                ("reference", models.CharField(db_index=True, max_length=80, unique=True)),
                ("description", models.CharField(max_length=255)),
                ("status", models.CharField(choices=[("POSTED", "Posted"), ("VOID", "Void")], db_index=True, default="POSTED", max_length=20)),
                ("object_id", models.PositiveIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("content_type", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="contenttypes.contenttype")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="journal_entries_created", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name_plural": "Journal entries",
                "ordering": ["-entry_date", "-id"],
            },
        ),
        migrations.CreateModel(
            name="JournalLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("debit", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("credit", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("memo", models.CharField(blank=True, max_length=255)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="journal_lines", to="accounting.chartofaccount")),
                ("entry", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lines", to="accounting.journalentry")),
            ],
            options={
                "ordering": ["entry_id", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="journalentry",
            index=models.Index(fields=["content_type", "object_id"], name="accounting_j_content_f04c0f_idx"),
        ),
        migrations.AddIndex(
            model_name="journalentry",
            index=models.Index(fields=["status", "entry_date"], name="accounting_j_status_86c307_idx"),
        ),
        migrations.AddIndex(
            model_name="journalline",
            index=models.Index(fields=["account"], name="accounting_j_account_84fe03_idx"),
        ),
    ]
