# Generated by Django 5.1 on 2024-10-17 13:27

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("otto", "0010_user_daily_max"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="user",
            name="daily_max",
        ),
        migrations.AddField(
            model_name="user",
            name="weekly_max",
            field=models.IntegerField(default=20),
        ),
        migrations.AddField(
            model_name="user",
            name="weekly_max_override",
            field=models.IntegerField(null=True),
        ),
    ]
