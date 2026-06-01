#!/usr/bin/env python
"""
Helper script to check document status from notebook.
Avoids async context issues with Django ORM.
"""

import json
import os
import sys

import django

from librarian.models import Document

# This assumes the script is run from the django directory
# and the project root is in the python path
sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "otto.settings")
django.setup()


def check_documents(document_ids):
    """
    Check status of documents and return counts.

    Args:
        document_ids: list of document IDs to check

    Returns:
        dict with status counts
    """
    # Count each status type
    success_count = 0
    error_count = 0
    processing_count = 0

    # Check each document one by one
    for doc_id in document_ids:
        try:
            doc = Document.objects.get(id=doc_id)

            # Is it done successfully?
            if doc.status == "SUCCESS":
                success_count = success_count + 1
            # Is it done with an error?
            elif doc.status == "ERROR":
                error_count = error_count + 1
            # Is it still being processed?
            else:
                processing_count = processing_count + 1

        except Document.DoesNotExist:
            # Document doesn't exist yet, count as processing
            processing_count = processing_count + 1

    # How many total?
    total_count = success_count + error_count + processing_count

    return {
        "total": total_count,
        "success": success_count,
        "error": error_count,
        "processing": processing_count,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No document IDs provided"}))
        sys.exit(1)

    # Parse document IDs from command line
    document_ids = [int(x) for x in sys.argv[1:]]

    result = check_documents(document_ids)
    print(json.dumps(result))
