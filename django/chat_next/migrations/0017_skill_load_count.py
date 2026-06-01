from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat_next", "0016_backfill_user_display_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="skill",
            name="load_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
