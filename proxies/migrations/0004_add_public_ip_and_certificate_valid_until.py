from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("proxies", "0003_rename_protocol_proxyconfig_backend_protocol_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="proxyconfig",
            name="public_ip",
            field=models.GenericIPAddressField(blank=True, null=True, protocol="both"),
        ),
        migrations.AddField(
            model_name="certificatebundle",
            name="valid_until",
            field=models.DateField(blank=True, null=True),
        ),
    ]