from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("quotes", "0003_quote_ck_quote_subtotal_gte_0_and_more")]

    operations = [
        migrations.AddField(
            model_name="quote",
            name="prices_include_tax_snapshot",
            field=models.BooleanField(default=False),
        ),
    ]
