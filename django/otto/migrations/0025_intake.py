# Generated by Django 5.1.1 on 2024-12-31 17:21

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("otto", "0024_alter_cost_feature"),
    ]

    operations = [
        migrations.CreateModel(
            name="Intake",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "urgency",
                    models.CharField(
                        choices=[
                            ("asap", "As soon as possible (ASAP)"),
                            ("eod", "End of day (EOD)"),
                            ("tomorrow", "Tomorrow"),
                            ("not_urgent", "Not urgent"),
                            ("other", "Other"),
                        ],
                        default="asap",
                        max_length=50,
                    ),
                ),
                ("doc_description", models.TextField()),
                ("purpose", models.TextField()),
                ("desired_info", models.TextField()),
                ("preferred_format", models.TextField()),
                ("further_details", models.TextField()),
                ("admin_notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("modified_on", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="intake",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="modified_intake",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
