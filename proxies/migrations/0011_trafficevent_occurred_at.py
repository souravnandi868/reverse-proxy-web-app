from django.db import migrations, models
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive


def backfill(apps, schema_editor):
    Event = apps.get_model("proxies", "TrafficEvent")
    events = Event.objects.using(schema_editor.connection.alias)
    batch = []
    for event in events.all().iterator(chunk_size=500):
        try:
            value = parse_datetime(str(event.data.get("time", "")))
        except ValueError:
            value = None
        if value is not None and not is_naive(value):
            event.occurred_at = value
            batch.append(event)
        if len(batch) >= 500:
            events.bulk_update(batch, ["occurred_at"])
            batch = []
    if batch:
        events.bulk_update(batch, ["occurred_at"])


class Migration(migrations.Migration):
    dependencies = [("proxies", "0010_servermonitor_history")]
    operations = [
        migrations.AddField(model_name="trafficevent", name="occurred_at",
                            field=models.DateTimeField(blank=True, db_index=True, null=True)),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
