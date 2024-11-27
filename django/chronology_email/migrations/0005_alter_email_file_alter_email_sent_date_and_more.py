# Generated by Django 5.1 on 2024-11-25 15:06

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chronology_email", "0004_thread"),
    ]

    operations = [
        migrations.AlterField(
            model_name="email",
            name="file",
            field=models.FileField(blank=True, null=True, upload_to="emails/"),
        ),
        migrations.AlterField(
            model_name="email",
            name="sent_date",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="email",
            name="thread_id",
            field=models.UUIDField(),
        ),
    ]
