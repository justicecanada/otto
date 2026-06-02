from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat_next", "0004_message_response_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="chat",
            name="code_interpreter_container_id",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
