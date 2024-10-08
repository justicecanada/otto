# Generated by Django 5.1 on 2024-09-23 14:57

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0015_chatoptions_chat_agent_alter_chatoptions_mode"),
        ("librarian", "0008_remove_datasource_chat"),
    ]

    operations = [
        migrations.AddField(
            model_name="chat",
            name="data_source",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="chat",
                to="librarian.datasource",
            ),
        ),
    ]
