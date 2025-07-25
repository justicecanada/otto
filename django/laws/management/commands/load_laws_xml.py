import sys
import time

from django.core.management.base import BaseCommand
from django.utils.timezone import localtime, now

from django_extensions.management.utils import signalcommand
from structlog import get_logger

from laws.loading_utils import calculate_job_elapsed_time
from laws.models import JobStatus, LawLoadingStatus
from laws.tasks import update_laws

logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Load laws XML from github"

    def add_arguments(self, parser):
        parser.add_argument(
            "--full", action="store_true", help="Performs a full load of all data"
        )
        parser.add_argument(
            "--small",
            action="store_true",
            help="Only loads the smallest 1 act and 1 regulation",
        )
        parser.add_argument(
            "--const_only",
            action="store_true",
            help="Only loads the constitution",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Resets the database before loading",
        )
        parser.add_argument(
            "--debug",
            action="store_true",
            help="Write node markdown to source directories. Does not alter database.",
        )
        parser.add_argument(
            "--mock_embedding",
            action="store_true",
            help="Mock embedding the nodes (save time/cost for debugging)",
        )
        parser.add_argument(
            "--force_update",
            action="store_true",
            help="Force update of existing laws (even if they have not changed)",
        )
        parser.add_argument(
            "--force_download",
            action="store_true",
            help="Force re-download of the laws-lois-xml repo, even if it exists",
        )
        parser.add_argument(
            "--cancel",
            action="store_true",
            help="Cancel any running law loading process automatically",
        )
        parser.add_argument(
            "--start",
            action="store_true",
            help="Start the law loading process, cancelling any existing job",
        )
        parser.add_argument(
            "--use_celery",
            action="store_true",
            help="Force use of Celery (default is to run synchronously)",
        )
        parser.add_argument(
            "--eng_law_ids",
            type=str,
            help="Comma-separated list of English law IDs to load (overrides --full, --small, --const_only)",
        )
        parser.add_argument(
            "--skip_purge",
            action="store_true",
            help="Skip purging laws not in the current load",
        )

    def print_status(self):

        def get_status():
            job_status = JobStatus.objects.singleton()
            law_statuses = LawLoadingStatus.objects.all().order_by("started_at")
            return job_status, law_statuses

        try:
            while True:
                job_status, law_statuses = get_status()
                # Clear screen
                print("\033[2J\033[H", end="")
                print(f"Job Status: {job_status.status}")
                if job_status.error_message:
                    print(f"Error: {job_status.error_message}")
                print(
                    f"Started: {localtime(job_status.started_at).strftime('%Y-%m-%d %H:%M:%S') if job_status.started_at else '-'}"
                )
                print(
                    f"Finished: {localtime(job_status.finished_at).strftime('%Y-%m-%d %H:%M:%S') if job_status.finished_at else '-'}"
                )
                # Add time elapsed
                elapsed_str = calculate_job_elapsed_time(job_status)
                print(f"Time Elapsed: {elapsed_str}")
                print("\nLaw Loading Progress:")
                total = law_statuses.count()
                finished = law_statuses.filter(status="finished").count()
                empty = law_statuses.filter(status="empty").count()
                error = law_statuses.filter(status="error").count()
                pending = law_statuses.filter(status="pending").count()
                parsing = law_statuses.filter(status="parsing_xml").count()
                embedding = law_statuses.filter(status="embedding_nodes").count()
                print(
                    f"  Total: {total} | Finished: {finished} | Empty: {empty} | Error: {error} | Pending: {pending} | Parsing: {parsing} | Embedding: {embedding}"
                )
                # Show a few recent statuses, prioritizing parsing/embedding
                print("\nRecent Laws:")
                parsing_or_embedding = law_statuses.filter(
                    status__in=["parsing_xml", "embedding_nodes"]
                ).order_by("-started_at")
                shown_ids = set()
                for ls in parsing_or_embedding:
                    print(
                        f"  {ls.eng_law_id or '-'}: {ls.status} {ls.details or ''} {ls.error_message or ''}"
                    )
                    shown_ids.add(ls.pk)
                # Show the rest, excluding those already shown
                recent_rest = law_statuses.exclude(pk__in=shown_ids).order_by(
                    "-started_at"
                )[: 10 - len(shown_ids)]
                for ls in recent_rest:
                    print(
                        f"  {ls.eng_law_id or '-'}: {ls.status} {ls.details or ''} {ls.error_message or ''}"
                    )
                if job_status.status in ["finished", "cancelled", "error"]:
                    print("\nJob complete.")
                    break
                print("\nPress Ctrl+C to exit status monitor.")
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStatus monitoring stopped by user.")

    @signalcommand
    def handle(self, *args, **options):
        job_status = JobStatus.objects.singleton()
        if options["cancel"] or options["start"]:
            job_status.cancel()
            print("Existing job cancelled.")
        # If job is running, show status
        if options["start"]:
            eng_law_ids = None
            if options.get("eng_law_ids"):
                eng_law_ids = [
                    x.strip() for x in options["eng_law_ids"].split(",") if x.strip()
                ]
            start_options = dict(
                small=options["small"],
                full=options["full"],
                const_only=options["const_only"],
                reset=options["reset"],
                force_download=options["force_download"],
                mock_embedding=options["mock_embedding"],
                debug=options["debug"],
                force_update=options["force_update"],
                skip_purge=options["skip_purge"],
            )
            if eng_law_ids:
                start_options["eng_law_ids"] = eng_law_ids
            if options["use_celery"]:
                print("Running via Celery...")
                update_laws.delay(**start_options)
            else:
                print("Running synchronously without Celery...")
                update_laws(**start_options)
                return

        if options["start"]:
            print("Job started. Monitoring status...")
            if not options["use_celery"]:
                print("Job completed synchronously.")
                return
            time.sleep(3)  # Allow time for the task to start
        try:
            self.print_status()
        except KeyboardInterrupt:
            print("\nStatus monitoring stopped by user.")
            sys.exit(0)
