import datetime

from django.core.management.base import BaseCommand

from django_extensions.management.utils import signalcommand

from chat.models import AnswerSource
from librarian.models import DataSource, Document, Library


class Command(BaseCommand):
    help = "Delete libraries which haven't been used in a QA answer for a certain period of time (default: 90 days)"

    def add_arguments(self, parser):
        # before date
        parser.add_argument(
            "--before",
            type=str,
            help="Delete libraries not accessed since this date. Format: YYYY-MM-DD",
        )

    @signalcommand
    def handle(self, *args, **options):
        # If "before" flag, delete before that date
        if options["before"]:
            delete_from = datetime.datetime.strptime(options["before"], "%Y-%m-%d")
        # By default, delete before 1 week ago
        else:
            delete_from = datetime.datetime.now() - datetime.timedelta(days=90)

        libraries_deleted = 0
        documents_deleted = 0

        # Get all libraries that haven't been accessed since the delete_from date
        # We have to check each Document answersource_set - for each answer, the message.date_created
        # is the date the answer was created
        # Also keep documents that have modified_at gte delete_from

        # Get document IDs that meet the criteria
        document_ids = list(
            AnswerSource.objects.filter(
                message__date_created__gte=delete_from, document_id__isnull=False
            ).values_list("document_id", flat=True)
        )

        # Get documents that have been modified since delete_from
        modified_documents = list(
            Document.objects.filter(modified_at__gte=delete_from).values_list(
                "id", flat=True
            )
        )

        # Combine the two sets of document IDs
        keep_documents = list(set(document_ids + modified_documents))

        # Get data source IDs for the kept documents
        keep_data_sources = list(
            Document.objects.filter(id__in=keep_documents).values_list(
                "data_source_id", flat=True
            )
        )

        # Get data sources that have been modified since delete_from
        modified_data_sources = list(
            DataSource.objects.filter(modified_at__gte=delete_from).values_list(
                "id", flat=True
            )
        )

        # Combine the two sets of data source IDs
        keep_data_sources = list(set(keep_data_sources + modified_data_sources))

        # Get library IDs for the kept data sources
        keep_libraries = list(
            DataSource.objects.filter(id__in=keep_data_sources).values_list(
                "library_id", flat=True
            )
        )

        # Get libraries that have been modified since delete_from
        modified_libraries = list(
            Library.objects.filter(modified_at__gte=delete_from).values_list(
                "id", flat=True
            )
        )

        # Combine the two sets of library IDs
        keep_libraries = list(set(keep_libraries + modified_libraries))

        # Get libraries to delete
        delete_libraries = Library.objects.exclude(id__in=keep_libraries)
        documents_deleted = Document.objects.filter(
            data_source__library__in=delete_libraries
        ).count()
        for library in delete_libraries:
            library.delete()
            libraries_deleted += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {libraries_deleted} libraries containing {documents_deleted} documents"
            )
        )
