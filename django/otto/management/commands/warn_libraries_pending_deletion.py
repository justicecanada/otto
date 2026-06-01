import datetime

# settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from django_extensions.management.utils import signalcommand

from otto.models import Notification
from otto.rules import GLOBAL_SKILL_DEFAULTS_LIBRARY_NAME_EN
from otto.settings import LIBRARY_RETENTION_DAYS, LIBRARY_WARN_BEFORE_DELETION_DAYS

from librarian.models import LibraryUserRole


class Command(BaseCommand):
    help = "Send notification warning of unused library deletion (default: 5 days) prior to deletion date."

    def add_arguments(self, parser):
        # number of days
        parser.add_argument(
            "--days",
            type=int,
            help="Send notification warning of unused library deletion this many days prior to deletion date",
        )

    @signalcommand
    def handle(self, *args, **options):
        if options["days"]:
            deletion_date = (
                timezone.now() + datetime.timedelta(days=options["days"])
            ).strftime("%Y-%m-%d")
            days_since_accessed = LIBRARY_RETENTION_DAYS - options["days"]
            notify_from = timezone.now() - datetime.timedelta(days=days_since_accessed)
        else:
            deletion_date = (
                timezone.now()
                + datetime.timedelta(days=LIBRARY_WARN_BEFORE_DELETION_DAYS)
            ).strftime("%Y-%m-%d")
            days_since_accessed = (
                LIBRARY_RETENTION_DAYS - LIBRARY_WARN_BEFORE_DELETION_DAYS
            )
            notify_from = timezone.now() - datetime.timedelta(days=days_since_accessed)

        user_roles = (
            LibraryUserRole.objects.filter(
                library__accessed_at__date=notify_from.date(),
                library__is_default_library=False,
                library__is_personal_library=False,
                library__is_skill_library=False,
                library__is_public=False,
            )
            .exclude(library__name_en=GLOBAL_SKILL_DEFAULTS_LIBRARY_NAME_EN)
            .filter(Q(role="admin") | Q(role="contributor"))
            .order_by("library")
        )

        for user_role in user_roles:
            library_name = user_role.library.name
            user = user_role.user
            Notification.objects.create(
                user=user,
                heading_en="Q&A library deletion",
                heading_fr="Suppression de la bibliothèque de questions et réponses",
                text_en=f"The Q&A library {library_name} has not been accessed for {days_since_accessed} days. It will be automatically deleted on {deletion_date}. To prevent deletion, select the library in Q&A mode and ask a question.",
                text_fr=f"La bibliothèque de questions et réponses, {library_name}, n'a pas été consultée depuis {days_since_accessed} jours. Elle sera automatiquement supprimée le {deletion_date}. Pour éviter la suppression, sélectionnez la bibliothèque en mode Q&R et posez une question",
                category="warning",
            )

        self.stdout.write(
            self.style.SUCCESS(f"Sent {user_roles.count()} notifications")
        )
