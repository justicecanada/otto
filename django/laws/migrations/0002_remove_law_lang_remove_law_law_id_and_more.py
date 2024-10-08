# Generated by Django 5.1 on 2024-09-16 13:05

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("laws", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="law",
            name="lang",
        ),
        migrations.RemoveField(
            model_name="law",
            name="law_id",
        ),
        migrations.AddField(
            model_name="law",
            name="enabling_authority_en",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="law",
            name="enabling_authority_fr",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="law",
            name="in_force_start_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="law",
            name="long_title_en",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="law",
            name="long_title_fr",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="law",
            name="node_id_en",
            field=models.CharField(max_length=255, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="law",
            name="node_id_fr",
            field=models.CharField(max_length=255, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="law",
            name="ref_number_en",
            field=models.CharField(max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="law",
            name="ref_number_fr",
            field=models.CharField(max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="law",
            name="sha_256_hash_en",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="law",
            name="sha_256_hash_fr",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="law",
            name="short_title_en",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="law",
            name="short_title_fr",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="law",
            name="title_en",
            field=models.TextField(null=True),
        ),
        migrations.AddField(
            model_name="law",
            name="title_fr",
            field=models.TextField(null=True),
        ),
        migrations.AlterField(
            model_name="law",
            name="current_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="law",
            name="last_amended_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
