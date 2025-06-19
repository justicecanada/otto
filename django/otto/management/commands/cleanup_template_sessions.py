import datetime
import os

# settings
from django.conf import settings
from django.core.management.base import BaseCommand

from django_extensions.management.utils import signalcommand

from template_wizard.models import TemplateSession


class Command(BaseCommand):
    help = (
        "Delete empty template sessions more than 1 day old and any older than 30 days."
    )

    def add_arguments(self, parser):
        # Delete all
        parser.add_argument(
            "--all_empty",
            action="store_true",
            help="Delete all empty template sessions, regardless of age.",
        )
        parser.add_argument(
            "--purge",
            action="store_true",
            help="Permanently delete all template sessions, regardless of age or emptiness.",
        )

    @signalcommand
    def handle(self, *args, **options):
        delete_from = datetime.datetime.now() - datetime.timedelta(days=1)

        if options["all_empty"]:
            sessions = TemplateSession.objects.filter(sources__isnull=True)
        else:
            sessions = TemplateSession.objects.filter(
                created_at__lt=delete_from, sources__isnull=True
            )

        num_sessions = sessions.count()
        sessions.delete()

        # Now delete any sessions older than 30 days, or all if --all is specified
        if options["purge"]:
            old_sessions = TemplateSession.objects.all()
        else:
            old_sessions = TemplateSession.objects.filter(
                created_at__lt=datetime.datetime.now() - datetime.timedelta(days=30)
            )

        num_old_sessions = old_sessions.count()
        old_sessions.delete()
        num_sessions += num_old_sessions

        self.stdout.write(
            self.style.SUCCESS(f"Deleted {num_sessions} template sessions")
        )
