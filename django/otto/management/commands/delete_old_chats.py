import datetime

# settings
from django.core.management.base import BaseCommand

from django_extensions.management.utils import signalcommand

from chat.models import Chat
from otto.settings import CHAT_RETENTION_DAYS


class Command(BaseCommand):
    help = "Delete chats more than (default: 30) days old."

    def add_arguments(self, parser):
        # before date
        parser.add_argument(
            "--before",
            type=str,
            help="Delete chat files older than this date. Format: YYYY-MM-DD",
        )
        # number of days
        parser.add_argument(
            "--days",
            type=int,
            help="Delete chat files older than this number of days.",
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
            delete_from = datetime.datetime.now() - datetime.timedelta(
                days=CHAT_RETENTION_DAYS
            )

        chats = Chat.objects.filter(accessed_at__lt=delete_from)

        num_chats = chats.count()
        chats.delete()

        self.stdout.write(self.style.SUCCESS(f"Deleted {num_chats} chats"))
