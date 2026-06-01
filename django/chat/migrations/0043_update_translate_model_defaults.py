from django.db import migrations, models


SUPPORTED_TRANSLATE_MODELS = [
    "azure",
    "azure_custom",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
]


def update_translate_models(apps, schema_editor):
    ChatOptions = apps.get_model("chat", "ChatOptions")
    ChatOptions.objects.exclude(
        translate_model__in=SUPPORTED_TRANSLATE_MODELS
    ).update(translate_model="gpt-5.4-mini")


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0042_alter_chatoptions_chat_reasoning_effort_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="chatoptions",
            name="translate_model",
            field=models.CharField(default="gpt-5.4-mini", max_length=20),
        ),
        migrations.RunPython(update_translate_models, migrations.RunPython.noop),
    ]
