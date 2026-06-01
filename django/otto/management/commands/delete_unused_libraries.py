import datetime

# settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from django_extensions.management.utils import signalcommand

from otto.rules import GLOBAL_SKILL_DEFAULTS_LIBRARY_NAME_EN
from otto.settings import LIBRARY_RETENTION_DAYS

from librarian.models import Library


class Command(BaseCommand):
    help = f"Delete libraries (and their data sources, documents, files) older than (default: {LIBRARY_RETENTION_DAYS}) days old."

    @staticmethod
    def _parse_cutoff(options):
        if options["days"]:
            return timezone.now() - datetime.timedelta(days=options["days"])

        if options["before"]:
            delete_from = datetime.datetime.strptime(options["before"], "%Y-%m-%d")
            if timezone.is_naive(delete_from):
                delete_from = timezone.make_aware(delete_from)
            return delete_from

        return timezone.now() - datetime.timedelta(days=LIBRARY_RETENTION_DAYS)

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
        delete_from = self._parse_cutoff(options)

        libraries = Library.objects.filter(accessed_at__lt=delete_from).exclude(
            Q(is_default_library=True)
            | Q(is_personal_library=True)
            | Q(is_public=True)
            | Q(is_skill_library=True)
            | Q(name_en=GLOBAL_SKILL_DEFAULTS_LIBRARY_NAME_EN)
        )

        num_libraries = libraries.count()
        libraries.delete()

        self.stdout.write(self.style.SUCCESS(f"Deleted {num_libraries} library"))
