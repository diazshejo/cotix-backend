import apps.tenants.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenants", "0007_stripe_webhook_event")]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="trial_ends_at",
            field=models.DateTimeField(default=apps.tenants.models.default_trial_ends_at),
        ),
    ]
