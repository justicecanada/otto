import datetime

# settings
from django.core.management.base import BaseCommand

from django_extensions.management.utils import signalcommand

from chat.models import Library


class Command(BaseCommand):
    help = "Delete libraries (and their data sources, documents, files) older than (default: 30) days old."

    def add_arguments(self, parser):
        # before date
        parser.add_argument(
            "--before",
            type=str,
            help="Delete libraries (and their data sources, documents, files) older than this date. Format: YYYY-MM-DD",
        )
        # number of days
        parser.add_argument(
            "--days",
            type=int,
            help="Delete libraries (and their data sources, documents, files) older than this number of days.",
        )

    @signalcommand
    def handle(self, *args, **options):
        if options["days"]:
            delete_from = datetime.datetime.now() - datetime.timedelta(
                days=options["days"]
            )
        elif options["before"]:
            delete_from = datetime.datetime.strptime(options["before"], "%Y-%m-%d")
        else:
            delete_from = datetime.datetime.now() - datetime.timedelta(days=30)

        libraries = Library.objects.filter(accessed_at__lt=delete_from).filter(
            is_public=False
        )

        num_libraries = libraries.count()
        libraries.delete()

        self.stdout.write(self.style.SUCCESS(f"Deleted {num_libraries} library"))
