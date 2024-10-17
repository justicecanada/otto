# Generated by Django 5.1 on 2024-10-17 15:06

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("otto", "0011_remove_user_daily_max_user_weekly_max_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="user",
            name="weekly_max_override",
        ),
        migrations.AddField(
            model_name="user",
            name="weekly_bonus",
            field=models.IntegerField(default=0),
        ),
    ]
