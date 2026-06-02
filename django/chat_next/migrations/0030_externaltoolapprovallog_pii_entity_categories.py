from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "chat_next",
            "0029_remove_externaltoolapprovallog_chat_next_external_tool_approval_log_per_event_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="externaltoolapprovallog",
            name="pii_entity_categories",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
