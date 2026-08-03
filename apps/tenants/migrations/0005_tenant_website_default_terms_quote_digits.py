from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0004_tenant_plan_free"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="website",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="tenant",
            name="default_terms",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="tenant",
            name="quote_digits",
            field=models.PositiveSmallIntegerField(default=4),
        ),
        migrations.AddConstraint(
            model_name="tenant",
            constraint=models.CheckConstraint(
                condition=models.Q(quote_digits__gte=1) & models.Q(quote_digits__lte=10),
                name="ck_tenant_quote_digits_1_10",
            ),
        ),
    ]
