import datetime

# settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from chat_next.models import Chat as NextChat
from django_extensions.management.utils import signalcommand

from otto.settings import CHAT_RETENTION_DAYS

from chat.models import Chat as LegacyChat


class Command(BaseCommand):
    help = f"Delete chats more than (default: {CHAT_RETENTION_DAYS}) days old."

    @staticmethod
    def _parse_cutoff(options):
        if options["days"]:
            return timezone.now() - datetime.timedelta(days=options["days"])

        if options["before"]:
            delete_from = datetime.datetime.strptime(options["before"], "%Y-%m-%d")
            if timezone.is_naive(delete_from):
                delete_from = timezone.make_aware(delete_from)
            return delete_from

        return timezone.now() - datetime.timedelta(days=CHAT_RETENTION_DAYS)

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
        delete_from = self._parse_cutoff(options)

        legacy_chats = LegacyChat.objects.filter(accessed_at__lt=delete_from).exclude(
            pinned=True
        )
        next_chats = NextChat.objects.filter(accessed_at__lt=delete_from).exclude(
            pinned=True
        )

        num_chats = legacy_chats.count() + next_chats.count()
        legacy_chats.delete()
        next_chats.delete()

        self.stdout.write(self.style.SUCCESS(f"Deleted {num_chats} chats"))
