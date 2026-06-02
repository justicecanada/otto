from django.db import migrations


OLD_DEFAULT_CATEGORIES = [
    "Address",
    "Email",
    "IPAddress",
    "NumericIdentifier",
    "Person",
    "PhoneNumber",
]

NEW_DEFAULT_CATEGORIES = [
    "Address",
    "Email",
    "IPAddress",
    "NumericIdentifier",
    "Organization",
    "Person",
    "PhoneNumber",
]


def enable_organization_default_category(apps, schema_editor):
    OttoStatus = apps.get_model("otto", "OttoStatus")

    status_rows = OttoStatus.objects.values_list(
        "pk", "external_tool_review_flagged_azure_pii_categories"
    )

    for status_id, categories in status_rows.iterator():
        categories = categories or []
        normalized_categories = [
            str(category).strip() for category in categories if str(category).strip()
        ]

        if not normalized_categories:
            OttoStatus.objects.filter(pk=status_id).update(
                external_tool_review_flagged_azure_pii_categories=NEW_DEFAULT_CATEGORIES
            )
            continue

        if set(normalized_categories) == set(OLD_DEFAULT_CATEGORIES) and len(
            normalized_categories
        ) == len(OLD_DEFAULT_CATEGORIES):
            OttoStatus.objects.filter(pk=status_id).update(
                external_tool_review_flagged_azure_pii_categories=NEW_DEFAULT_CATEGORIES
            )


class Migration(migrations.Migration):

    dependencies = [
        ("otto", "0027_ottostatus_external_tool_review_flag_credentials_or_secrets_and_more"),
    ]

    operations = [
        migrations.RunPython(enable_organization_default_category, migrations.RunPython.noop),
    ]
