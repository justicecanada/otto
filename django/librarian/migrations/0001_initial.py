# Generated by Django 5.0.7 on 2024-07-25 20:33

import librarian.models
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="DataSource",
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
                    "uuid_hex",
                    models.CharField(
                        default=librarian.models.generate_uuid_hex,
                        editable=False,
                        max_length=32,
                        unique=True,
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                ("name_en", models.CharField(max_length=255, null=True)),
                ("name_fr", models.CharField(max_length=255, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("modified_at", models.DateTimeField(auto_now=True)),
                ("order", models.IntegerField(default=0)),
            ],
            options={
                "ordering": ["order", "name"],
            },
        ),
        migrations.CreateModel(
            name="Document",
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
                    "uuid_hex",
                    models.CharField(
                        default=librarian.models.generate_uuid_hex,
                        editable=False,
                        max_length=32,
                        unique=True,
                    ),
                ),
                ("sha256_hash", models.CharField(blank=True, max_length=64, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Not started"),
                            ("INIT", "Starting..."),
                            ("PROCESSING", "Processing..."),
                            ("SUCCESS", "Success"),
                            ("ERROR", "Error"),
                            ("BLOCKED", "Stopped"),
                        ],
                        default="PENDING",
                        max_length=20,
                    ),
                ),
                (
                    "celery_task_id",
                    models.CharField(blank=True, max_length=50, null=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("modified_at", models.DateTimeField(auto_now=True)),
                (
                    "extracted_title",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                ("extracted_modified_at", models.DateTimeField(blank=True, null=True)),
                (
                    "generated_title",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                ("generated_description", models.TextField(blank=True, null=True)),
                (
                    "manual_title",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                ("extracted_text", models.TextField(blank=True, null=True)),
                ("num_chunks", models.IntegerField(blank=True, null=True)),
                ("url", models.URLField(blank=True, null=True)),
                ("selector", models.CharField(blank=True, max_length=255, null=True)),
                ("fetched_at", models.DateTimeField(blank=True, null=True)),
                (
                    "url_content_type",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                ("filename", models.CharField(blank=True, max_length=255, null=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="Library",
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
                    "uuid_hex",
                    models.CharField(
                        default=librarian.models.generate_uuid_hex,
                        editable=False,
                        max_length=32,
                        unique=True,
                    ),
                ),
                ("name", models.CharField(blank=True, max_length=255, null=True)),
                ("name_en", models.CharField(blank=True, max_length=255, null=True)),
                ("name_fr", models.CharField(blank=True, max_length=255, null=True)),
                ("description", models.TextField(blank=True, null=True)),
                ("description_en", models.TextField(blank=True, null=True)),
                ("description_fr", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("modified_at", models.DateTimeField(auto_now=True)),
                ("accessed_at", models.DateTimeField(auto_now_add=True)),
                ("order", models.IntegerField(default=0)),
                ("is_public", models.BooleanField(default=False)),
            ],
            options={
                "verbose_name_plural": "Libraries",
                "ordering": ["-is_public", "order", "name"],
            },
        ),
        migrations.CreateModel(
            name="LibraryUserRole",
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
                    "role",
                    models.CharField(
                        choices=[
                            ("admin", "Admin"),
                            ("contributor", "Contributor"),
                            ("viewer", "Viewer"),
                        ],
                        max_length=20,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="SavedFile",
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
                    "sha256_hash",
                    models.CharField(
                        blank=True, db_index=True, max_length=64, null=True
                    ),
                ),
                ("file", models.FileField(upload_to="files/%Y/%m/%d/")),
                ("content_type", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("eof", models.BooleanField(default=True)),
            ],
        ),
    ]