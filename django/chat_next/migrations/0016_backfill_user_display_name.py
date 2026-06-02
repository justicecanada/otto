# Generated migration to backfill user_display_name in ChatSettings

from django.db import migrations


def backfill_user_display_names(apps, schema_editor):
    """Populate user_display_name from user.full_name for existing ChatSettings that are blank."""
    ChatSettings = apps.get_model("chat_next", "ChatSettings")

    for settings in ChatSettings.objects.filter(user_display_name=""):
        user_full_name = ""
        try:
            # Construct full_name from first_name and last_name
            user = settings.user
            user_full_name = f"{user.first_name} {user.last_name}".strip()
        except Exception:
            pass

        if user_full_name:
            settings.user_display_name = user_full_name
            settings.save(update_fields=["user_display_name"])


class Migration(migrations.Migration):

    dependencies = [
        ("chat_next", "0015_backfill_chat_settings_defaults"),
    ]

    operations = [
        migrations.RunPython(
            backfill_user_display_names,
            migrations.RunPython.noop,
        ),
    ]
