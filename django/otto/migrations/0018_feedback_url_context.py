# Generated by Django 5.1 on 2024-11-21 17:10

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("otto", "0017_feedback_admin_notes_feedback_created_by_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="feedback",
            name="url_context",
            field=models.CharField(blank=True, max_length=2048),
        ),
    ]
