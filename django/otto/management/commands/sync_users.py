# settings
import asyncio

from django.core.management.base import BaseCommand

from django_extensions.management.utils import signalcommand

from otto.utils.entra import sync_users_with_entra_async


class Command(BaseCommand):
    help = "Sync users between Otto and Entra"

    @signalcommand
    def handle(self, *args, **options):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(sync_users_with_entra_async())
        self.stdout.write(self.style.SUCCESS("Users sync completed successfully."))
