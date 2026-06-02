from django.db import migrations


def reset_terms_acceptance_for_all_users(apps, schema_editor):
    User = apps.get_model("otto", "User")

    User.objects.exclude(accepted_terms_date=None).update(accepted_terms_date=None)


class Migration(migrations.Migration):

    dependencies = [
        ("otto", "0030_rename_data_steward_group_and_upload_limit_fields"),
    ]

    operations = [
        migrations.RunPython(
            reset_terms_acceptance_for_all_users,
            migrations.RunPython.noop,
        ),
    ]
