# Generated by Django 5.1 on 2024-08-28 18:16

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("otto", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CostType",
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
                ("name", models.CharField(max_length=100)),
                ("name_en", models.CharField(max_length=100, null=True)),
                ("name_fr", models.CharField(max_length=100, null=True)),
                ("short_name", models.CharField(max_length=50, null=True, unique=True)),
                ("description", models.TextField()),
                ("description_en", models.TextField(null=True)),
                ("description_fr", models.TextField(null=True)),
                ("unit_name", models.CharField(default="units", max_length=50)),
                (
                    "unit_name_en",
                    models.CharField(default="units", max_length=50, null=True),
                ),
                (
                    "unit_name_fr",
                    models.CharField(default="units", max_length=50, null=True),
                ),
                (
                    "unit_cost",
                    models.DecimalField(decimal_places=6, default=1, max_digits=10),
                ),
                ("unit_quantity", models.IntegerField(default=1)),
            ],
        ),
        migrations.AddField(
            model_name="feature",
            name="short_name",
            field=models.CharField(max_length=50, null=True, unique=True),
        ),
        migrations.CreateModel(
            name="Cost",
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
                ("count", models.IntegerField(default=1)),
                ("usd_cost", models.DecimalField(decimal_places=6, max_digits=12)),
                ("date_incurred", models.DateTimeField(auto_now_add=True)),
                (
                    "feature",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="otto.feature"
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "cost_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="otto.costtype"
                    ),
                ),
            ],
        ),
    ]