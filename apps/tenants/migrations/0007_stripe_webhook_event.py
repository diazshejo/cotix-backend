from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenants", "0006_tenant_stripe_fields")]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="stripe_subscription_created",
            field=models.BigIntegerField(default=0),
        ),
        migrations.CreateModel(
            name="StripeWebhookEvent",
            fields=[
                ("event_id", models.CharField(max_length=255, primary_key=True, serialize=False)),
                ("event_type", models.CharField(max_length=100)),
                ("processed_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
