from django.core.cache import cache
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Disable the /load_test/ endpoint."

    def handle(self, *args, **options):
        cache.set("load_testing_enabled", False)
        self.stdout.write(self.style.SUCCESS("Load testing disabled"))
