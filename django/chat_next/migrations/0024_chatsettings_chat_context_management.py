from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat_next", "0023_alter_chatsettings_chat_model_default"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatsettings",
            name="chat_context_management",
            field=models.CharField(
                choices=[
                    ("compact", "Compact"),
                    ("truncate", "Truncate"),
                    ("error", "Show error"),
                ],
                default="compact",
                max_length=10,
            ),
        ),
    ]
