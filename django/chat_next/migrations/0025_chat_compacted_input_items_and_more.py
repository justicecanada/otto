from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat_next", "0024_chatsettings_chat_context_management"),
    ]

    operations = [
        migrations.AddField(
            model_name="chat",
            name="compacted_input_items",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="chat",
            name="compacted_through_message",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="+",
                to="chat_next.message",
            ),
        ),
    ]
