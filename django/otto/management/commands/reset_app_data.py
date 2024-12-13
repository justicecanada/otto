import os
import subprocess

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import BaseCommand

import yaml
from django_extensions.management.utils import signalcommand

from chat.models import ChatOptions, Preset
from librarian.models import DataSource, Document, Library
from otto.models import App, UsageTerm, User


class Command(BaseCommand):
    help = "Reset otto and librarian model instances based on YAML configuration"

    def add_arguments(self, parser):
        parser.add_argument(
            "objects",
            nargs="*",
            type=str,
            help="Specify objects to reset (apps, terms, groups, libraries, library_mini, security_labels, cost_types)",
        )
        parser.add_argument("--all", action="store_true", help="Reset all objects")

    @signalcommand
    def handle(self, *args, **options):
        reset_all = options.get("all", False)
        objects_to_reset = options.get("objects", [])

        # Create the database DATABASES["vector_db"] if it doesn't exist
        vector_db_name = settings.DATABASES["vector_db"]["NAME"]
        vector_db_user = settings.DATABASES["vector_db"]["USER"]
        vector_db_password = settings.DATABASES["vector_db"]["PASSWORD"]
        vector_db_host = settings.DATABASES["vector_db"]["HOST"]

        # Set system-wide environment variable PGPASSWORD to avoid password prompt
        os.environ["PGPASSWORD"] = vector_db_password

        try:
            # Create the database
            subprocess.run(
                [
                    "psql",
                    "-U",
                    vector_db_user,
                    "-h",
                    vector_db_host,
                    "-d",
                    "postgres",
                    "-c",
                    f"CREATE DATABASE {vector_db_name}",
                ],
                check=True,
            )
        except subprocess.CalledProcessError:
            self.stdout.write(
                self.style.WARNING(
                    f"Database {vector_db_name} already exists. Skipping creation."
                )
            )

        if reset_all:
            self.reset_groups()
            self.reset_apps()
            self.reset_usage_terms()
            self.reset_security_labels()
            self.reset_libraries()
            self.reset_cost_types()
        else:
            if "groups" in objects_to_reset:
                self.reset_groups()

            if "apps" in objects_to_reset:
                self.reset_apps()

            if "terms" in objects_to_reset:
                self.reset_usage_terms()

            if "cost_types" in objects_to_reset:
                self.reset_cost_types()

            if "security_labels" in objects_to_reset:
                self.reset_security_labels()

            if "libraries" in objects_to_reset:
                self.reset_libraries()

            if "library_mini" in objects_to_reset:
                self.reset_libraries("library_mini.yaml")
            if "presets" in objects_to_reset:
                self.reset_presets()

    def reset_apps(self):
        yaml_file_path = os.path.join(
            settings.BASE_DIR, "otto", "fixtures", "apps.yaml"
        )

        with open(yaml_file_path, "r", encoding="utf-8") as yaml_file:
            apps_data = yaml.safe_load(yaml_file)

        if not apps_data:
            self.stdout.write(
                self.style.WARNING(
                    "No data found in the YAML file. Nothing to reset for Apps."
                )
            )
            return

        # Clear out existing App instances
        App.objects.all().delete()

        # Create new App instances based on YAML data
        for app_data in apps_data:
            App.objects.create_from_yaml(app_data)

        self.stdout.write(self.style.SUCCESS("Apps reset successfully."))

    def reset_usage_terms(self):
        yaml_file_path = os.path.join(
            settings.BASE_DIR, "otto", "fixtures", "terms.yaml"
        )

        with open(yaml_file_path, "r", encoding="utf-8") as yaml_file:
            terms_data = yaml.safe_load(yaml_file)

        if not terms_data:
            self.stdout.write(
                self.style.WARNING(
                    "No data found in the YAML file. Nothing to reset for UsageTerms."
                )
            )
            return

        # Clear out existing UsageTerm instances
        UsageTerm.objects.all().delete()

        # Create new UsageTerm instances based on YAML data
        for term_data in terms_data:
            term_fields = term_data.get("fields", {})
            UsageTerm.objects.create(**term_fields)

        self.stdout.write(self.style.SUCCESS("UsageTerms reset successfully."))

    def reset_groups(self):
        yaml_file_path = os.path.join(
            settings.BASE_DIR, "otto", "fixtures", "groups.yaml"
        )

        with open(yaml_file_path, "r", encoding="utf-8") as yaml_file:
            groups_data = yaml.safe_load(yaml_file)

        if not groups_data:
            self.stdout.write(
                self.style.WARNING(
                    "No data found in the YAML file. Nothing to reset for groups."
                )
            )
            return

        # Clear out existing groups and user groups
        Group.objects.all().delete()

        # Create new groups based on YAML data
        for group_data in groups_data:
            group_fields = group_data.get("fields", {})
            group_instance = Group.objects.create(**group_fields)

            # Add permissions to the group if specified in the YAML file
            permissions = group_data.get("permissions", [])
            for codename in permissions:
                # Retrieve the permission instance
                permission_instance = Permission.objects.get(codename=codename)
                group_instance.permissions.add(permission_instance)

            # Add users to the group if specified in the YAML file
            users = group_data.get("users", [])
            for upn in users:
                user_instance = (
                    get_user_model().objects.filter(upn=upn).first()
                ) or get_user_model().objects.create_user(upn)
                group_instance.user_set.add(user_instance)

        self.stdout.write(
            self.style.SUCCESS("Groups and user groups reset successfully.")
        )

    def reset_libraries(self, yaml_file_name="library.yaml"):
        yaml_file_path = os.path.join(
            settings.BASE_DIR, "librarian", "fixtures", yaml_file_name
        )

        with open(yaml_file_path, "r", encoding="utf-8") as yaml_file:
            libraries_data = yaml.safe_load(yaml_file)

        if not libraries_data:
            self.stdout.write(
                self.style.WARNING(
                    "No data found in the YAML file. Nothing to reset for libraries."
                )
            )
            return

        # Clear out existing instances
        Document.objects.all().delete()
        DataSource.objects.all().delete()
        Library.objects.all().delete()

        Library.objects.reset_vector_store()

        for item in libraries_data:
            if item["model"] != "librarian.library":
                continue

            library_fields = item.get("fields", {})
            data_sources = library_fields.pop("data_sources", [])

            library_instance = Library.objects.create(**library_fields)

            for data_source in data_sources:
                data_source_fields = data_source.get("fields", {})
                documents = data_source_fields.pop("documents", [])

                data_source_instance = DataSource.objects.create(
                    library=library_instance, **data_source_fields
                )

                for document in documents:
                    document_fields = document.get("fields", {})
                    Document.objects.create(
                        data_source=data_source_instance, **document_fields
                    )

        self.stdout.write(
            self.style.SUCCESS(
                "Libraries, DataSources, and Documents reset successfully."
            )
        )

    def reset_presets(self):
        yaml_file_path = os.path.join(
            settings.BASE_DIR, "otto", "fixtures", "default_preset.yaml"
        )

        with open(yaml_file_path, "r", encoding="utf-8") as yaml_file:
            presets_data = yaml.safe_load(yaml_file)

        # Delete existing presets and associated options
        Preset.objects.all().delete()
        ChatOptions.objects.filter(preset__isnull=False).delete()

        # TODO - figure out who should be the owner of the default preset, for now I'm setting myself
        owner = User.objects.filter(email="Michel.Custeau@justice.gc.ca").first()

        # Create ChatOptions with prompts from the YAML file
        default_library = Library.objects.get_default_library()
        chat_options = ChatOptions.objects.create(
            mode="chat",
            chat_system_prompt=presets_data["default_chat_prompt"]["en"],
            qa_system_prompt=presets_data["qa_system_prompt"]["en"],
            qa_prompt_template=presets_data["qa_prompt_template"]["en"],
            qa_pre_instructions=presets_data["qa_pre_instructions"]["en"],
            qa_post_instructions=presets_data["qa_post_instructions"]["en"],
            chat_model=settings.DEFAULT_CHAT_MODEL,
            qa_model=settings.DEFAULT_QA_MODEL,
            summarize_model=settings.DEFAULT_SUMMARIZE_MODEL,
            qa_library=default_library,
        )

        Preset.objects.create(
            name_en="Default Preset",
            name_fr="Préréglage par défaut",
            description_en="Default preset including default prompts",
            description_fr="Préréglage par défaut incluant les invites par défaut",
            options=chat_options,
            owner=owner,
            sharing_option="everyone",
        )
        self.stdout.write(self.style.SUCCESS("Presets reset successfully."))

    def reset_security_labels(self):
        # Simply call manage.py loaddata security_labels.yaml
        call_command("loaddata", "security_labels.yaml")
        self.stdout.write(self.style.SUCCESS("Security labels reset successfully."))

    def reset_cost_types(self):
        # Simply call manage.py loaddata cost_types.yaml
        call_command("loaddata", "cost_types.yaml")
        self.stdout.write(self.style.SUCCESS("Cost types reset successfully."))
