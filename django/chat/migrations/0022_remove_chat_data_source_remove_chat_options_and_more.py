# Generated by Django 5.1 on 2024-10-11 14:44

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0021_merge_20241008_1956"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="chat",
            name="data_source",
        ),
        migrations.RemoveField(
            model_name="chat",
            name="options",
        ),
        migrations.AddField(
            model_name="chatoptions",
            name="chat",
            field=models.OneToOneField(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="options",
                to="chat.chat",
            ),
        ),
    ]
