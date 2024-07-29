# call command
import os
import subprocess

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection

from django_extensions.management.utils import signalcommand


class Command(BaseCommand):
    help = "Delete every database table, then migrate again."

    # Get force argument
    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip confirmation and delete the database.",
        )

    @signalcommand
    def handle(self, *args, **options):
        tables = connection.introspection.table_names()
        if not options["yes"]:
            confirm = input(
                "Delete all data in the Otto database? YOU HAVE BEEN WARNED! (yes/no): "
            )
            if confirm != "yes":
                self.stdout.write("Aborting.")
                return
        with connection.cursor() as cursor:
            for table in tables:
                cursor.execute(f"DROP TABLE {table} CASCADE")
        self.stdout.write("All tables dropped.")
        self.stdout.write("Running migrations...")
        call_command("migrate")
        self.stdout.write("Done deleting Otto database.")
        # Delete vector database
        if not options["yes"]:
            confirm = input(
                "Delete all data in the Vector database? YOU HAVE BEEN WARNED! (yes/no): "
            )
            if confirm != "yes":
                self.stdout.write("Aborting.")
                return
        # Use settings.DATABASES["vector_db"]
        vector_db_name = settings.DATABASES["vector_db"]["NAME"]
        vector_db_user = settings.DATABASES["vector_db"]["USER"]
        vector_db_password = settings.DATABASES["vector_db"]["PASSWORD"]
        vector_db_host = settings.DATABASES["vector_db"]["HOST"]

        # Set system-wide environment variable PGPASSWORD to avoid password prompt
        os.environ["PGPASSWORD"] = vector_db_password

        try:
            # Drop the database
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
                    f"DROP DATABASE {vector_db_name}",
                ],
                check=True,
            )
        except subprocess.CalledProcessError:
            self.stdout.write(
                self.style.WARNING(f"Database {vector_db_name} could not be deleted.")
            )
        self.stdout.write("Done deleting Vector database.")

        self.stdout.write(
            "All done. Recommend running `bash initial_setup.sh` from django root."
        )
