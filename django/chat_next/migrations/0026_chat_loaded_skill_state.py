from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat_next", "0025_chat_compacted_input_items_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="chat",
            name="loaded_skill_state",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
