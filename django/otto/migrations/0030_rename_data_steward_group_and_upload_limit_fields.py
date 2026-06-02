from django.db import migrations, models


OLD_GROUP_NAME = "Data steward"
NEW_GROUP_NAME = "Bulk uploader"


def _rename_or_merge_group(apps, old_name, new_name):
    Group = apps.get_model("auth", "Group")

    old_group = Group.objects.filter(name=old_name).first()
    new_group = Group.objects.filter(name=new_name).first()

    if old_group and new_group:
        new_group.user_set.add(*old_group.user_set.all())
        new_group.permissions.add(*old_group.permissions.all())
        old_group.delete()
        return

    if old_group and not new_group:
        old_group.name = new_name
        old_group.save(update_fields=["name"])


def rename_data_steward_to_bulk_uploader(apps, schema_editor):
    _rename_or_merge_group(apps, OLD_GROUP_NAME, NEW_GROUP_NAME)


def rename_bulk_uploader_to_data_steward(apps, schema_editor):
    _rename_or_merge_group(apps, NEW_GROUP_NAME, OLD_GROUP_NAME)


def _table_columns(schema_editor, table_name):
    with schema_editor.connection.cursor() as cursor:
        return {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor, table_name
            )
        }


def _rename_column_if_needed(schema_editor, table_name, old_name, new_name):
    columns = _table_columns(schema_editor, table_name)
    if old_name not in columns or new_name in columns:
        return

    quoted_table_name = schema_editor.quote_name(table_name)
    quoted_old_name = schema_editor.quote_name(old_name)
    quoted_new_name = schema_editor.quote_name(new_name)
    schema_editor.execute(
        f"ALTER TABLE {quoted_table_name} RENAME COLUMN {quoted_old_name} TO {quoted_new_name};"
    )


def rename_upload_limit_columns_forward(apps, schema_editor):
    _rename_column_if_needed(
        schema_editor,
        "otto_ottostatus",
        "steward_chat_max_mb",
        "bulk_uploader_chat_max_mb",
    )
    _rename_column_if_needed(
        schema_editor,
        "otto_ottostatus",
        "steward_librarian_max_mb",
        "bulk_uploader_librarian_max_mb",
    )


def rename_upload_limit_columns_backward(apps, schema_editor):
    _rename_column_if_needed(
        schema_editor,
        "otto_ottostatus",
        "bulk_uploader_chat_max_mb",
        "steward_chat_max_mb",
    )
    _rename_column_if_needed(
        schema_editor,
        "otto_ottostatus",
        "bulk_uploader_librarian_max_mb",
        "steward_librarian_max_mb",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("otto", "0029_user_entra_status_job_title_preferred_language"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(
            rename_data_steward_to_bulk_uploader,
            rename_bulk_uploader_to_data_steward,
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    rename_upload_limit_columns_forward,
                    rename_upload_limit_columns_backward,
                )
            ],
            state_operations=[
                migrations.RenameField(
                    model_name="ottostatus",
                    old_name="steward_chat_max_mb",
                    new_name="bulk_uploader_chat_max_mb",
                ),
                migrations.RenameField(
                    model_name="ottostatus",
                    old_name="steward_librarian_max_mb",
                    new_name="bulk_uploader_librarian_max_mb",
                ),
            ],
        ),
        migrations.AlterField(
            model_name="ottostatus",
            name="bulk_uploader_chat_max_mb",
            field=models.IntegerField(
                blank=True,
                default=50,
                help_text="Max chat upload size (MB) for Bulk uploaders",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="ottostatus",
            name="bulk_uploader_librarian_max_mb",
            field=models.IntegerField(
                blank=True,
                default=500,
                help_text="Max librarian upload size (MB) for Bulk uploaders",
                null=True,
            ),
        ),
    ]
