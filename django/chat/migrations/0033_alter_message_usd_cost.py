# Generated by Django 5.1.1 on 2024-11-12 20:25

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0032_chatoptions_prompt"),
    ]

    operations = [
        migrations.AlterField(
            model_name="message",
            name="usd_cost",
            field=models.DecimalField(decimal_places=4, max_digits=10, null=True),
        ),
    ]
