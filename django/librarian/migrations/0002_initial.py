# Generated by Django 5.0.7 on 2024-07-25 20:33

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("chat", "0003_initial"),
        ("librarian", "0001_initial"),
        ("otto", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="datasource",
            name="security_label",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="otto.securitylabel",
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="data_source",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="documents",
                to="librarian.datasource",
            ),
        ),
        migrations.AddField(
            model_name="library",
            name="chat",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="chat.chat",
            ),
        ),
        migrations.AddField(
            model_name="library",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="datasource",
            name="library",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="data_sources",
                to="librarian.library",
            ),
        ),
        migrations.AddField(
            model_name="libraryuserrole",
            name="library",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="user_roles",
                to="librarian.library",
            ),
        ),
        migrations.AddField(
            model_name="libraryuserrole",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="file",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="documents",
                to="librarian.savedfile",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="libraryuserrole",
            unique_together={("library", "user")},
        ),
    ]
