# settings
import asyncio

from django.core.management.base import BaseCommand

from django_extensions.management.utils import signalcommand

from otto.utils.entra import sync_users_with_entra_async


class Command(BaseCommand):
    help = "Sync users between Otto and Entra"

    @signalcommand
    def handle(self, *args, **options):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError as e:
            if str(e).startswith("There is no current event loop"):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            else:
                raise
        loop.run_until_complete(sync_users_with_entra_async())
        loop.close()
        self.stdout.write(self.style.SUCCESS("Users sync completed successfully."))
