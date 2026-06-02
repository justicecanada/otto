from django.conf import settings
from django.db import migrations



def create_otto_user_group(apps, schema_editor):
    """Create the 'Otto user' group and add all active users to it."""
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("otto", "User")

    group, _ = Group.objects.get_or_create(name=settings.OTTO_USER_GROUP)
    
    if settings.ENVIRONMENT.lower() == "prod":

        # Add all currently users to the group
        users = User.objects.all()
        group.user_set.add(*users)


def remove_otto_user_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=settings.OTTO_USER_GROUP).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("otto", "0014_add_cost_indexes"),
    ]

    operations = [
        migrations.RunPython(
            create_otto_user_group,
            reverse_code=remove_otto_user_group,
        ),
    ]
