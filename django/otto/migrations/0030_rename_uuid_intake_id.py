# Generated by Django 5.1.1 on 2025-01-02 21:51

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("otto", "0029_remove_intake_id_intake_uuid"),
    ]

    operations = [
        migrations.RenameField(
            model_name="intake",
            old_name="uuid",
            new_name="id",
        ),
    ]