from django.core.management.base import BaseCommand

from django_extensions.management.utils import signalcommand

from librarian.models import Library


class Command(BaseCommand):
    help = "Load the corporate library"

    @signalcommand
    def handle(self, *args, **options):
        Library.objects.get(name="Corporate").process_all()
        self.stdout.write(self.style.SUCCESS("Corporate library loaded successfully."))
