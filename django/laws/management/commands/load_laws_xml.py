import hashlib
import os
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils.timezone import datetime

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
from laws.tasks import load_laws
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

    @signalcommand
    def handle(self, *args, **options):
        total_cost = 0
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
