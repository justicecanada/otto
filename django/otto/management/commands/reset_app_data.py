import os
import subprocess

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import BaseCommand

import yaml
from django_extensions.management.utils import signalcommand

from chat.models import Preset
from librarian.models import DataSource, Document, Library
from otto.models import App


class Command(BaseCommand):
    help = "Reset otto and librarian model instances based on YAML configuration"

    def add_arguments(self, parser):
        parser.add_argument(
            "objects",
            nargs="*",
            type=str,
            help="Specify objects to reset (apps, groups, libraries, library_mini, security_labels, cost_types, presets)",
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
        vector_db_port = settings.DATABASES["vector_db"]["PORT"]

        # Set system-wide environment variable PGPASSWORD to avoid password prompt
        os.environ["PGPASSWORD"] = vector_db_password

        if settings.ENVIRONMENT == "LOCAL":
            try:
                # Create the vector database (local only)
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
            self.reset_security_labels()
            self.reset_libraries("library_mini.yaml")
            self.reset_cost_types()
            self.reset_presets()
        else:
            if "groups" in objects_to_reset:
                self.reset_groups()

            if "apps" in objects_to_reset:
                self.reset_apps()

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
            settings.BASE_DIR, "chat", "fixtures", "presets.yaml"
        )

        with open(yaml_file_path, "r", encoding="utf-8") as yaml_file:
            presets_data = yaml.safe_load(yaml_file)

        # Delete existing "default presets" (which have no owner)
        Preset.objects.filter(owner=None).delete()
        Preset.objects.create_from_yaml(presets_data)

        self.stdout.write(self.style.SUCCESS("Presets reset successfully."))

    def reset_security_labels(self):
        # Simply call manage.py loaddata security_labels.yaml
        call_command("loaddata", "security_labels.yaml")
        self.stdout.write(self.style.SUCCESS("Security labels reset successfully."))

    def reset_cost_types(self):
        # Simply call manage.py loaddata cost_types.yaml
        call_command("loaddata", "cost_types.yaml")
        self.stdout.write(self.style.SUCCESS("Cost types reset successfully."))
