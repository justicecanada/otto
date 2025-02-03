import datetime

# settings
from django.core.management.base import BaseCommand
from django.db.models import Q

from django_extensions.management.utils import signalcommand

from librarian.models import LibraryUserRole
from otto.models import Notification


class Command(BaseCommand):
    help = "Send notification warning of unused library deletion (default: 5) prior to deletion date."

    def add_arguments(self, parser):
        # number of days
        parser.add_argument(
            "--days",
            type=int,
            help="Send notification warning of unused library deletion this days prior to deletion date",
        )

    @signalcommand
    def handle(self, *args, **options):

        if options["days"]:
            deletion_date = (
                datetime.datetime.now() + datetime.timedelta(days=options["days"])
            ).strftime(f"%Y-%m-%d")
            days_since_accessed = 30 - options["days"]
            notify_from = datetime.datetime.now() - datetime.timedelta(
                days_since_accessed
            )
        else:
            deletion_date = (
                datetime.datetime.now() + datetime.timedelta(days=5)
            ).strftime(f"%Y-%m-%d")
            days_since_accessed = 30 - 5
            notify_from = datetime.datetime.now() - datetime.timedelta(
                days=days_since_accessed
            )

        user_roles = (
            LibraryUserRole.objects.filter(
                library__accessed_at__date__lte=notify_from.date(),
                library__is_default_library=False,
                library__is_personal_library=False,
            )
            .filter(Q(role="admin") | Q(role="contributor"))
            .order_by("library")
        )

        for user_role in user_roles:
            library_name = user_role.library.name
            user = user_role.user
            Notification.objects.create(
                user=user,
                heading="Q&A library deletion",
                text=f"The Q&A library ${library_name} has not been accessed for 25 days. It will be automatically deleted on ${deletion_date}. To prevent deletion, select the library in Q&A mode and ask a question.",
                category="warning",
            )

        self.stdout.write(
            self.style.SUCCESS(f"Sent {user_roles.count()} notifications")
        )
