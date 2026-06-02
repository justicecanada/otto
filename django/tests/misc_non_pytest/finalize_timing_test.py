#!/usr/bin/env python
"""
Simple test to measure finalize_document_light performance using real production code.

The existing librarian_embedding_log.csv already logs insert_nodes timing
(batch_size, seconds, error_code, retry_after) in DEBUG mode.

This script just creates test documents and queues them for processing,
then you can analyze librarian_embedding_log.csv to see the timing breakdown.
"""

import os
import random
import sys

import django
from django.conf import settings

from otto import priorities

from librarian.models import DataSource, Document, Library
from librarian.tasks import finalize_document_light

# Assume this script is run from the django directory in the container/pod
# Use the current working directory as the project root to avoid hardcoded paths
sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "otto.settings")
django.setup()


def create_test_chunks(num_chunks=100):
    """Create test text chunks of ~750 tokens."""
    base_words = "pizza at park prince zen cat be jump on it".split()  # 10 words
    # Shuffle them to avoid caching
    random.shuffle(base_words)
    chunks = []
    for i in range(num_chunks):
        # Create a list of 750 words by repeating the base words
        words = base_words * 75
        # Join them into a single string
        chunk = " ".join(words)
        chunks.append(chunk)
    return chunks


def queue_test_documents(
    num_documents=5, min_chunks=10, max_chunks=300, mock_embedding=False, seed=42
):
    """
    Queue test documents to finalize_document_light.

    This uses the REAL production task which logs to librarian_embedding_log.csv
    """
    import random

    random.seed(seed)

    # Get or create a test library
    test_library, _ = Library.objects.get_or_create(
        name="Test Library (Timing)",
    )

    # Get or create a test data source
    test_source, _ = DataSource.objects.get_or_create(
        library=test_library,
        name="Test Source (Timing)",
    )

    document_ids = []

    for i in range(num_documents):
        num_chunks = random.randint(min_chunks, max_chunks)

        # Create test document
        document = Document.objects.create(
            data_source=test_source,
            filename=f"test_timing_{i}_{num_chunks}chunks.txt",
            status="TEXT_EXTRACTED",
            extracted_text=f"Test document {i} with {num_chunks} chunks",
        )

        # Create test chunks
        chunks = create_test_chunks(num_chunks)

        # Queue finalize task
        finalize_document_light.apply_async(
            kwargs={
                "document_id": document.id,
                "chunks": chunks,
                "mock_embedding": mock_embedding,
            },
            priority=priorities.LOW,
        )

        document_ids.append(document.id)
        print(
            f"Queued document {i + 1}/{num_documents}: {num_chunks} chunks, document_id={document.id}"
        )

    log_path = getattr(
        settings,
        "LIBRARIAN_EMBEDDING_LOG_PATH",
        os.path.join(settings.MEDIA_ROOT, "librarian_embedding_log.csv"),
    )

    print(f"\n✓ Queued {num_documents} documents")
    print(f"  Library: {test_library.name} (id={test_library.id})")
    print(f"\nTiming data will be in: {log_path}")
    print("Columns: batch_size, seconds, error_code, retry_after")

    return document_ids, test_library.id


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test finalize_document_light timing")
    parser.add_argument(
        "--num-docs", type=int, default=5, help="Number of documents to create"
    )
    parser.add_argument(
        "--min-chunks", type=int, default=10, help="Min chunks per document"
    )
    parser.add_argument(
        "--max-chunks", type=int, default=300, help="Max chunks per document"
    )
    parser.add_argument(
        "--mock-embedding",
        action="store_true",
        help="Use mock embedding instead of real API",
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="Random seed for reproducibility"
    )

    args = parser.parse_args()

    queue_test_documents(
        num_documents=args.num_docs,
        min_chunks=args.min_chunks,
        max_chunks=args.max_chunks,
        mock_embedding=args.mock_embedding,
        seed=args.seed,
    )
