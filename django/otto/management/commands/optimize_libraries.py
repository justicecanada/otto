from django.conf import settings
from django.core.management.base import BaseCommand

from django_extensions.management.utils import signalcommand
from structlog import get_logger

from librarian.models import Library
from librarian.tasks import build_hnsw_index

logger = get_logger(__name__)


class Command(BaseCommand):
    help = """Optimize libraries by building HNSW indexes where needed.
    
    This command:
    - Updates total_chunks for all libraries
    - Builds HNSW indexes for libraries that should use them but don't have them
    - Skips libraries that already have indexes or are too small
    
    Intended to run nightly as part of maintenance.
    """

    def add_arguments(self, parser):
        parser.add_argument(
            "--library-id",
            type=int,
            help="Only optimize a specific library by ID",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force rebuild even if index already exists",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without actually building indexes",
        )

    @signalcommand
    def handle(self, *args, **options):
        library_id = options.get("library_id")
        force = options.get("force", False)
        dry_run = options.get("dry_run", False)

        if library_id:
            libraries = Library.objects.filter(id=library_id)
            if not libraries.exists():
                self.stdout.write(
                    self.style.ERROR(f"Library with ID {library_id} not found")
                )
                return
        else:
            # All libraries except personal libraries (too small/transient)
            libraries = Library.objects.filter(is_personal_library=False)

        self.stdout.write(f"Analyzing {libraries.count()} libraries...")

        to_build = []
        already_optimized = []
        too_small = []

        for library in libraries:
            # Update total chunks
            old_total = library.total_chunks
            library.update_total_chunks()

            if library.total_chunks != old_total:
                self.stdout.write(
                    f"  {library.name or library.uuid_hex}: "
                    f"{old_total:,} -> {library.total_chunks:,} chunks"
                )

            should_use = library.should_use_hnsw()
            has_index = library.hnsw_status == "ready"

            if should_use and (not has_index or force):
                to_build.append(library)
            elif has_index:
                already_optimized.append(library)
            else:
                too_small.append(library)

        # Report
        self.stdout.write("\n" + self.style.SUCCESS("Summary:"))
        self.stdout.write(
            f"  {len(to_build)} libraries need HNSW index "
            + f"(threshold: {settings.HNSW_THRESHOLD:,} chunks)"
        )
        self.stdout.write(f"  {len(already_optimized)} libraries already optimized")
        self.stdout.write(f"  {len(too_small)} libraries below threshold")

        if to_build:
            self.stdout.write("\n" + self.style.WARNING("Libraries to optimize:"))
            for lib in to_build:
                status = (
                    "rebuild"
                    if force and lib.hnsw_status == "ready"
                    else lib.hnsw_status
                )
                self.stdout.write(
                    f"  - {lib.name or lib.uuid_hex} "
                    f"({lib.total_chunks:,} chunks, status: {status})"
                )

            if not dry_run:
                self.stdout.write("\nBuilding indexes...")
                for lib in to_build:
                    if force and lib.hnsw_status == "ready":
                        # Drop existing index before rebuild
                        self.stdout.write(
                            f"  Dropping existing index for {lib.name}..."
                        )
                        lib.hnsw_status = "none"
                        lib.save(update_fields=["hnsw_status"])

                    self.stdout.write(
                        f"  Triggering build for {lib.name or lib.uuid_hex}..."
                    )
                    lib.hnsw_status = "pending"
                    lib.save(update_fields=["hnsw_status"])
                    result = build_hnsw_index.delay(lib.uuid_hex)
                    lib.hnsw_task_id = result.id
                    lib.save(update_fields=["hnsw_task_id"])
                    self.stdout.write(f"    Task ID: {result.id}")

                self.stdout.write(
                    "\n"
                    + self.style.SUCCESS(f"Triggered {len(to_build)} HNSW index builds")
                )
            else:
                self.stdout.write(
                    "\n" + self.style.WARNING("Dry run - no indexes were built")
                )
        else:
            self.stdout.write("\n" + self.style.SUCCESS("All libraries optimized!"))
