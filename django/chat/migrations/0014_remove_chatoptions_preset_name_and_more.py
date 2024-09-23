# Generated by Django 5.1 on 2024-09-06 13:22

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0013_rename_cost_message_usd_cost"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveField(
            model_name="chatoptions",
            name="preset_name",
        ),
        migrations.RemoveField(
            model_name="chatoptions",
            name="user",
        ),
        migrations.RemoveField(
            model_name="chatoptions",
            name="user_default",
        ),
        migrations.CreateModel(
            name="Preset",
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
                ("name_en", models.CharField(blank=True, max_length=255)),
                ("name_fr", models.CharField(blank=True, max_length=255)),
                ("description_en", models.TextField(blank=True)),
                ("description_fr", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_public", models.BooleanField(default=False)),
                ("is_deleted", models.BooleanField(default=False)),
                (
                    "accessible_to",
                    models.ManyToManyField(
                        related_name="accessible_presets", to=settings.AUTH_USER_MODEL
                    ),
                ),
                (
                    "default_for",
                    models.ManyToManyField(
                        related_name="default_presets", to=settings.AUTH_USER_MODEL
                    ),
                ),
                (
                    "editable_by",
                    models.ManyToManyField(
                        related_name="editable_presets", to=settings.AUTH_USER_MODEL
                    ),
                ),
                (
                    "favourited_by",
                    models.ManyToManyField(
                        related_name="favourited_presets", to=settings.AUTH_USER_MODEL
                    ),
                ),
                (
                    "options",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="preset",
                        to="chat.chatoptions",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]