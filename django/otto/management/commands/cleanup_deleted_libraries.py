from django.core.management.base import BaseCommand

from django_extensions.management.utils import signalcommand


class Command(BaseCommand):
    help = "Hard-delete libraries that were soft-deleted by users. Run nightly."

    @signalcommand
    def handle(self, *args, **options):
        from librarian.models import Library

        pending = Library.objects.including_deleted().filter(deleted_at__isnull=False)
        count = pending.count()

        for library in pending:
            try:
                library.hard_delete()
            except Exception as e:
                self.stderr.write(
                    self.style.ERROR(f"Failed to delete library {library.id}: {e}")
                )

        self.stdout.write(self.style.SUCCESS(f"Hard-deleted {count} libraries"))
