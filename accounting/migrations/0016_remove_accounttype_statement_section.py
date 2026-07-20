from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0015_ensure_default_financial_statement_natures"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="accounttype",
            name="statement_section",
        ),
        migrations.DeleteModel(
            name="FinancialStatementSection",
        ),
        migrations.AlterModelOptions(
            name="accounttype",
            options={"ordering": ["account_nature", "name"]},
        ),
    ]
