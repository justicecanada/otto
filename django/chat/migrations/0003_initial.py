# Generated by Django 5.1.5 on 2025-02-12 16:23

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("chat", "0002_initial"),
        ("librarian", "0001_initial"),
        ("otto", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="chat",
            name="security_label",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="otto.securitylabel",
            ),
        ),
        migrations.AddField(
            model_name="chat",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL
            ),
        ),
        migrations.AddField(
            model_name="chatfile",
            name="saved_file",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="chat_files",
                to="librarian.savedfile",
            ),
        ),
        migrations.AddField(
            model_name="chatoptions",
            name="chat",
            field=models.OneToOneField(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="options",
                to="chat.chat",
            ),
        ),
        migrations.AddField(
            model_name="chatoptions",
            name="qa_data_sources",
            field=models.ManyToManyField(
                related_name="qa_options", to="librarian.datasource"
            ),
        ),
        migrations.AddField(
            model_name="chatoptions",
            name="qa_documents",
            field=models.ManyToManyField(
                related_name="qa_options", to="librarian.document"
            ),
        ),
        migrations.AddField(
            model_name="chatoptions",
            name="qa_library",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="qa_options",
                to="librarian.library",
            ),
        ),
        migrations.AddField(
            model_name="message",
            name="chat",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="messages",
                to="chat.chat",
            ),
        ),
        migrations.AddField(
            model_name="message",
            name="parent",
            field=models.OneToOneField(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="child",
                to="chat.message",
            ),
        ),
        migrations.AddField(
            model_name="chatfile",
            name="message",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="files",
                to="chat.message",
            ),
        ),
        migrations.AddField(
            model_name="answersource",
            name="message",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to="chat.message"
            ),
        ),
        migrations.AddField(
            model_name="preset",
            name="accessible_to",
            field=models.ManyToManyField(
                related_name="accessible_presets", to=settings.AUTH_USER_MODEL
            ),
        ),
        migrations.AddField(
            model_name="preset",
            name="favourited_by",
            field=models.ManyToManyField(
                related_name="favourited_presets", to=settings.AUTH_USER_MODEL
            ),
        ),
        migrations.AddField(
            model_name="preset",
            name="options",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="preset",
                to="chat.chatoptions",
            ),
        ),
        migrations.AddField(
            model_name="preset",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="chat",
            name="loaded_preset",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="chat.preset",
            ),
        ),
        migrations.AddConstraint(
            model_name="message",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("parent__isnull", False), ("is_bot", True)),
                    ("parent__isnull", True),
                    _connector="OR",
                ),
                name="check_parent_is_user_message",
            ),
        ),
    ]
