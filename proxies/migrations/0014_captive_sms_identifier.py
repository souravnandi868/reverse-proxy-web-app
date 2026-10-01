from django.db import migrations, models
from django.db.models import Q


def disable_incomplete_users(apps, schema_editor):
    user_model = apps.get_model("proxies", "CaptivePortalUser")
    incomplete = (
        Q(mobile_number__isnull=True) | Q(mobile_number="")
        | Q(email_address__isnull=True) | Q(email_address="")
    )
    user_model.objects.filter(deleted_at__isnull=True).filter(incomplete).update(is_enabled=False)


class Migration(migrations.Migration):

    dependencies = [
        ("proxies", "0013_captive_portal"),
    ]

    operations = [
        migrations.RunPython(disable_incomplete_users, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="captiveportaluser",
            name="captive_contact_required",
        ),
        migrations.AlterField(
            model_name="captiveportaluser",
            name="mobile_number",
            field=models.CharField(max_length=13, null=True, unique=True),
        ),
        migrations.AlterField(
            model_name="captiveportaluser",
            name="email_address",
            field=models.EmailField(max_length=254, null=True),
        ),
        migrations.AlterField(
            model_name="captiveotp",
            name="channel",
            field=models.CharField(choices=[("sms", "SMS")], default="sms", max_length=5),
        ),
        migrations.AddConstraint(
            model_name="captiveportaluser",
            constraint=models.CheckConstraint(
                condition=(Q(deleted_at__isnull=False) | Q(is_enabled=False)
                           | Q(mobile_number__isnull=False, email_address__isnull=False)),
                name="captive_contact_required",
            ),
        ),
    ]