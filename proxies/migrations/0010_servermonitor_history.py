from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("proxies", "0009_proxyconfig_websocket_enabled")]

    operations = [
        migrations.AddField(
            model_name="servermonitor",
            name="history",
            field=models.JSONField(default=list),
        ),
    ]
