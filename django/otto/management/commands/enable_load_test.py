from django.core.cache import cache
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Enable the /load_test/ endpoint for a limited duration (seconds)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--duration",
            type=int,
            default=3600,
            help="Duration in seconds to keep load testing enabled (default: 3600)",
        )

    def handle(self, *args, **options):
        duration = options["duration"]
        cache.set("load_testing_enabled", True, timeout=duration)
        self.stdout.write(
            self.style.SUCCESS(f"Load testing enabled for {duration} seconds")
        )
