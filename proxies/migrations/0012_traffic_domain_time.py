from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("proxies", "0011_trafficevent_occurred_at")]
    operations = [
        migrations.AddIndex(
            model_name="trafficevent",
            index=models.Index(fields=["domain", "occurred_at"], name="traffic_domain_time"),
        ),
    ]
