# settings
import asyncio

from django.core.management.base import BaseCommand

from django_extensions.management.utils import signalcommand

from otto.utils.entra import sync_users_with_entra


class Command(BaseCommand):
    help = "Sync users between Otto and Entra"

    @signalcommand
    def handle(self, *args, **options):
        sync_users_with_entra()
        self.stdout.write(self.style.SUCCESS("Users sync completed successfully."))
