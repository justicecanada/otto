import time

from django.core.management.base import BaseCommand
from django.db import connections
from django.db.utils import OperationalError


class Command(BaseCommand):
    help = "Waits for the database to be available"

    def handle(self, *args, **options):
        self.stdout.write("Waiting for database...")
        db_conn = None
        timeout_limit = 30  # 5 minutes
        while not db_conn:
            try:
                db_conn = connections["default"]
            except OperationalError:
                self.stdout.write("Database unavailable, waiting 10 seconds...")
                time.sleep(10)
                timeout_limit -= 1

            if timeout_limit == 0:
                self.stdout.write(self.style.ERROR("Database unavailable!"))
                raise OperationalError("Database unavailable!")

        self.stdout.write(self.style.SUCCESS("Database available!"))
