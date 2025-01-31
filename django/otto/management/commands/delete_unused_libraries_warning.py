import datetime

# settings
from django.core.management.base import BaseCommand

from django_extensions.management.utils import signalcommand

from librarian.models import Library


class Command(BaseCommand):
    help = "Send notification warning of unused library deletion (default: 5) prior to deletion date."

    def add_arguments(self, parser):
        # number of days
        parser.add_argument(
            "--days",
            type=int,
            help="Send notification warning of unused library deletion this days prior to deletion date",
        )

    @signalcommand
    def handle(self, *args, **options):
        
        if options["days"]:
            days_since_accessed = 30 - days=options["days"]
            notify_from = datetime.datetime.now() - datetime.timedelta(
                days_since_accessed
            )
        else:
            days_since_accessed = 30 - 5
            notify_from = datetime.datetime.now() - datetime.timedelta(days=days_since_accessed)

        libraries = Library.objects.filter(accessed_at__lt=notify_from)

        #num_chats = chats.count()
        #chats.delete()

        #self.stdout.write(self.style.SUCCESS(f"Deleted {num_chats} chats"))
