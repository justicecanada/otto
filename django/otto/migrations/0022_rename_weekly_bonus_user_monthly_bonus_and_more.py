# Generated by Django 5.1.1 on 2024-12-06 20:52

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("otto", "0021_merge_20241127_1803"),
    ]

    operations = [
        migrations.RenameField(
            model_name="user",
            old_name="weekly_bonus",
            new_name="monthly_bonus",
        ),
        migrations.RemoveField(
            model_name="user",
            name="weekly_max",
        ),
        migrations.AddField(
            model_name="user",
            name="monthly_max",
            field=models.IntegerField(default=40),
        ),
    ]
