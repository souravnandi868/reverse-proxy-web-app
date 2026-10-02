# OTP browser binding and captive session fingerprint protection.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('proxies', '0014_captive_sms_identifier'),
    ]

    operations = [
        migrations.AddField(
            model_name='captiveotp',
            name='client_fingerprint',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='captivesession',
            name='client_fingerprint',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
    ]
