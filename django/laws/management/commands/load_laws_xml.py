import hashlib
import os
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils.timezone import now

import requests
from django_extensions.management.utils import signalcommand
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import (
    Document,
    MetadataMode,
    NodeRelationship,
    RelatedNodeInfo,
    TextNode,
)
from lxml import etree as ET
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from chat.llm import OttoLLM
from laws.models import Law
from laws.tasks import (
    cancel_law_loading,
    get_law_loading_status,
    initiate_law_loading_task,
    load_laws,
)
from otto.models import Cost, OttoStatus
from otto.utils.common import display_cad_cost

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
            "--accept_reset",
            action="store_true",
            help="Accept the reset of the database",
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
            "--skip_cleanup",
            action="store_true",
            help="Skip cleanup of the laws-lois-xml repo",
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
            "--reset_hnsw",
            action="store_true",
            help="Delete the HNSW index before loading, then recreate after loading",
        )
        parser.add_argument(
            "--retry_failed",
            action="store_true",
            help="Retry only laws that failed in the previous run",
        )
        parser.add_argument(
            "--use_celery",
            action="store_true",
            help="Use Celery tasks for asynchronous processing (recommended)",
        )
        parser.add_argument(
            "--status",
            action="store_true",
            help="Monitor law loading progress in real-time (refreshes every second, press Ctrl+C to exit)",
        )
        parser.add_argument(
            "--cancel",
            action="store_true",
            help="Cancel any running law loading process",
        )

    @signalcommand
    def handle(self, *args, **options):
        # Check status first if requested
        if options.get("status", False):
            self._display_status_loop()
            return

        # Handle cancellation if requested
        if options.get("cancel", False):
            print("Cancelling law loading process...")
            result = cancel_law_loading()

            if result["status"] == "success":
                print("✅ " + result["message"])
                if result.get("cancellation_task_id"):
                    print(f"Cancellation task ID: {result['cancellation_task_id']}")
                print("Use --status to monitor cancellation progress.")
            else:
                print("❌ " + result["message"])
            # Automatically enter status loop after cancel
            self._display_status_loop()
            return

        full = options.get("full", False)
        reset = options.get("reset", False)
        accept_reset = options.get("accept_reset", False)
        if reset:
            if not accept_reset:
                # Confirm the user wants to delete all laws
                print("This will delete all laws in the database. Are you sure?")
                response = input("Type 'yes' to confirm: ")
                if response != "yes":
                    print("Aborted")
                    return

        small = options.get("small", False)
        const_only = options.get("const_only", False)
        force_update = options.get("force_update", False)
        force_download = options.get("force_download", False)
        debug = options.get("debug", False)
        mock_embedding = options.get("mock_embedding", False)
        reset_hnsw = options.get("reset_hnsw", False)
        skip_cleanup = options.get("skip_cleanup", False)
        retry_failed = options.get("retry_failed", False)
        use_celery = options.get("use_celery", False)

        if use_celery:
            # Use new Celery-based processing
            print("Starting law loading with Celery tasks...")
            result = initiate_law_loading_task.apply_async(
                kwargs={
                    "small": small,
                    "full": full,
                    "const_only": const_only,
                    "reset": reset,
                    "force_download": force_download,
                    "mock_embedding": mock_embedding,
                    "debug": debug,
                    "force_update": force_update,
                    "retry_failed": retry_failed,
                }
            )
            print(f"Law loading task queued with ID: {result.id}")
            print("You can monitor progress via the OttoStatus.laws_status field")
            print("or use --status to check current progress.")
            print("Use --retry_failed --use_celery to retry failed laws.")
            # Automatically enter status loop
            self._display_status_loop()
        else:
            # Use legacy synchronous processing
            print("Using legacy synchronous processing...")
            print(
                "Note: Consider using --use_celery for better error handling and progress tracking"
            )
            load_laws(
                small=small,
                full=full,
                const_only=const_only,
                reset=reset,
                force_download=force_download,
                mock_embedding=mock_embedding,
                debug=debug,
                force_update=force_update,
                skip_cleanup=skip_cleanup,
                reset_hnsw=reset_hnsw,
            )

    def _display_status_loop(self):
        """
        Display law loading status in a loop that refreshes every second.
        Press Ctrl+C to exit.
        """
        import sys

        print("Law Loading Status Monitor")
        print("Press Ctrl+C to exit")
        print("-" * 50)

        try:
            while True:
                # Clear the screen (works on most terminals)
                os.system("clear" if os.name == "posix" else "cls")

                print("Law Loading Status Monitor")
                print("Press Ctrl+C to exit")
                print("=" * 50)

                status = get_law_loading_status()

                # Status with emoji
                status_emoji = {
                    "idle": "💤",
                    "downloading": "⬇️",
                    "running": "🔄",
                    "completed": "✅",
                    "failed": "❌",
                }
                current_status = status["status"]
                emoji = status_emoji.get(current_status, "📊")
                print(f"{emoji} Status: {current_status.title()}")

                # Progress information
                total = status.get("total_laws", 0)
                failed = status.get("failed", 0)
                empty = status.get("empty", 0)
                processing = status.get("processing", 0)
                queued = status.get("queued", 0)
                unchanged = status.get(
                    "unchanged", 0
                )  # Laws skipped because they already exist

                # Count completed, empty, and failed as processed
                processed = status.get("processed", 0) + empty + failed
                # Calculate progress percent including empty and failed as complete
                if total > 0:
                    progress = 100 * processed / total
                else:
                    progress = 0

                print(f"Progress: {progress:.1f}% ({processed}/{total} completed)")

                # Progress bar
                if total > 0:
                    bar_width = 40
                    filled = int(bar_width * progress / 100)
                    bar = "█" * filled + "░" * (bar_width - filled)
                    print(f"📊 [{bar}] {progress:.1f}%")

                print(f"✅ Completed: {processed}")
                if unchanged > 0:
                    print(f"🟡 Unchanged: {unchanged}")
                if processing > 0:
                    print(f"🔄 Processing: {processing}")
                if queued > 0:
                    print(f"⏳ Queued: {queued}")
                if empty > 0:
                    print(f"📄 Empty: {empty}")
                if failed > 0:
                    print(f"❌ Failed: {failed}")
                else:
                    print(f"❌ Failed: {failed}")

                # Timing information
                if status.get("started_at"):
                    print(f"🚀 Started: {status['started_at']}")
                if status.get("completed_at"):
                    print(f"🏁 Completed: {status['completed_at']}")
                elif status.get("started_at") and status["status"] == "running":
                    # Calculate elapsed time

                    try:
                        started = datetime.fromisoformat(
                            status["started_at"].replace("Z", "+00:00")
                        )
                        elapsed = datetime.now(started.tzinfo) - started
                        elapsed_str = str(elapsed).split(".")[0]  # Remove microseconds
                        print(f"⏱️  Elapsed: {elapsed_str}")

                        # Estimate remaining time if we have progress
                        if progress > 0 and progress < 100:
                            total_estimated = elapsed.total_seconds() * 100 / progress
                            remaining = total_estimated - elapsed.total_seconds()
                            remaining_str = str(timedelta(seconds=int(remaining)))
                            print(f"⏳ Estimated remaining: {remaining_str}")
                    except:
                        pass  # If datetime parsing fails, just skip elapsed time

                # Error details for failed laws
                if status.get("failed_law_ids"):
                    failed_laws = status["failed_law_ids"]
                    print(f"\n💥 Failed Laws ({len(failed_laws)}):")
                    for failed_law in failed_laws:
                        if isinstance(failed_law, dict):
                            law_id = failed_law.get("law_id", "Unknown")
                            error = failed_law.get("error", "Unknown error")
                            print(f"  • {law_id}: {error}")
                        else:
                            # Backward compatibility for simple string IDs
                            print(f"  • {failed_law}")

                # Deleted laws information
                if status.get("deleted_laws"):
                    deleted_laws = status["deleted_laws"]
                    if len(deleted_laws) <= 3:
                        print(f"\n🗑️ Deleted Laws: {', '.join(deleted_laws)}")
                    else:
                        print(
                            f"\n🗑️ Deleted Laws: {', '.join(deleted_laws[:3])} (and {len(deleted_laws)-3} more)"
                        )

                # Empty laws information
                if status.get("empty_law_ids"):
                    empty_laws = status["empty_law_ids"]
                    if len(empty_laws) <= 3:
                        print(f"\n📄 Empty Laws: {', '.join(empty_laws)}")
                    else:
                        print(
                            f"\n📄 Empty Laws: {', '.join(empty_laws[:3])} (and {len(empty_laws)-3} more)"
                        )

                # Individual progress bars for processing laws
                if status.get("processing_laws"):
                    processing_laws = status["processing_laws"]
                    print(f"\n� Processing Laws:")
                    # Limit to 5 laws to avoid screen overflow
                    display_laws = processing_laws[:5]
                    for proc_law in display_laws:
                        law_id = proc_law.get("law_id", "Unknown")
                        details = proc_law.get("details", "")
                        progress_text = proc_law.get("progress", "")

                        # Try to extract progress from details (e.g., "batch 84/769")
                        progress_bar = ""
                        if "batch" in details:
                            try:
                                # Extract current/total from "batch X/Y" format
                                batch_part = details.split("batch ")[1]
                                current, total = map(int, batch_part.split("/"))
                                progress_percent = (current / total) * 100
                                bar_width = 20  # Smaller bars for individual laws
                                filled = int(bar_width * progress_percent / 100)
                                progress_bar = f"[{'█' * filled}{'░' * (bar_width - filled)}] {progress_percent:.0f}%"
                            except:
                                progress_bar = f"[{details}]"
                        else:
                            progress_bar = f"[{progress_text}]"

                        print(f"  {law_id}: {progress_bar}")

                    if len(processing_laws) > 5:
                        print(f"  ... and {len(processing_laws) - 5} more")

                # Current law being processed (if not already shown above)
                if status.get("current_law") and not status.get("processing_laws"):
                    print(f"\n📖 Current: {status['current_law']}")

                print("=" * 50)
                print(f"🕐 Last updated: {datetime.now().strftime('%H:%M:%S')}")

                # Exit if completed or failed
                if status["status"] in ["completed", "failed", "idle"]:
                    if status["status"] == "completed":
                        print("\n✅ Law loading completed successfully!")
                    elif status["status"] == "failed":
                        print("\n❌ Law loading failed!")
                    else:
                        print("\n📝 No law loading process currently running.")
                    break

                # Wait for 1 second before next update
                time.sleep(1)

        except KeyboardInterrupt:
            print("\n\nStatus monitoring stopped by user.")
            sys.exit(0)
