import datetime
import os

# settings
from django.conf import settings
from django.core.management.base import BaseCommand

from django_extensions.management.utils import signalcommand

from chat.models import Chat


class Command(BaseCommand):
    help = "Delete empty chat files more than 1 day old."

    def add_arguments(self, parser):
        # Delete all
        parser.add_argument(
            "--all",
            action="store_true",
            help="Delete all empty chats, regardless of age.",
        )

    @signalcommand
    def handle(self, *args, **options):
        delete_from = datetime.datetime.now() - datetime.timedelta(days=1)

        if options["all"]:
            chats = Chat.objects.filter(messages__isnull=True)
        else:
            chats = Chat.objects.filter(
                created_at__lt=delete_from, messages__isnull=True
            )

        num_chats = chats.count()
        chats.delete()

        self.stdout.write(self.style.SUCCESS(f"Deleted {num_chats} chats"))
