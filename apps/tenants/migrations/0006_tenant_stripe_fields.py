from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0005_tenant_website_default_terms_quote_digits"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="stripe_customer_id",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="tenant",
            name="stripe_subscription_id",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
