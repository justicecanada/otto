#!/usr/bin/env python
"""
Runs a timing test for document finalization and embedding.

This script queues a number of test documents for processing,
waits for them to complete, and ensures the timing data is logged
to a CSV file located under Django's MEDIA_ROOT (or an override path).

It mirrors the core of analyze_embedding_timing.ipynb without notebook/UI code.
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime

import django
from django.conf import settings

# Assume this script is executed from the django directory in the container/pod
# so the project root is the current working directory (no hardcoded /workspace paths)
sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "otto.settings")
django.setup()

# Make sure we can import companion helpers from the same directory
HELPERS_DIR = os.path.join(os.getcwd(), "tests", "misc_non_pytest")
if HELPERS_DIR not in sys.path:
    sys.path.insert(0, HELPERS_DIR)

import check_document_status as cds  # noqa: E402
import finalize_timing_test as fft  # noqa: E402


def clear_log_file():
    """Deletes the existing log file if it exists."""
    log_path = getattr(
        settings,
        "LIBRARIAN_EMBEDDING_LOG_PATH",
        os.path.join(settings.MEDIA_ROOT, "librarian_embedding_log.csv"),
    )
    # Ensure directory exists
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    if os.path.exists(log_path):
        os.remove(log_path)
        print(f"Removed old log file: {log_path}")
    return log_path


def wait_for_documents(document_ids, poll_seconds=5):
    """Polls document status until all are processed."""
    print(f"Waiting for {len(document_ids)} documents to process...")
    while True:
        status = cds.check_documents(document_ids)
        if status["processing"] == 0:
            print("\nDocument processing complete.")
            print(f"  Success: {status['success']}")
            print(f"  Error: {status['error']}")
            break
        else:
            print(
                f"  -> In progress: {status['processing']} processing, "
                f"{status['success']} success, {status['error']} error..."
            )
            time.sleep(poll_seconds)


def parse_embedding_log(log_path):
    """Parse the embedding batch log and return summary metrics.

    Returns a dict with keys:
      - total_batches: int
      - total_chunks_success: int (sum of batch_size for non-error rows)
      - num_429: int
      - num_non_429_errors: int
      - avg_429_wait: float (seconds; 0.0 if none)
    """
    metrics = {
        "total_batches": 0,
        "total_chunks_success": 0,
        "num_429": 0,
        "num_non_429_errors": 0,
        "avg_429_wait": 0.0,
        "avg_latency_success": 0.0,
    }

    if not os.path.exists(log_path):
        return metrics

    total_429_wait = 0.0
    count_429 = 0

    with open(log_path, newline="") as f:
        # Peek first line to detect header presence
        first_line = f.readline()
        f.seek(0)

        total_success_seconds = 0.0
        count_success = 0

        has_header = "batch_size" in first_line and "seconds" in first_line

        if has_header:
            reader = csv.DictReader(f)
            for row in reader:
                metrics["total_batches"] += 1

                # batch_size
                try:
                    batch_size = int(row.get("batch_size", 0) or 0)
                except (ValueError, TypeError):
                    batch_size = 0

                error_raw = (row.get("error_code") or "").strip()
                error_num = None
                if error_raw != "":
                    try:
                        error_num = int(error_raw)
                    except ValueError:
                        error_num = None

                if error_raw == "" or error_raw == "0":
                    metrics["total_chunks_success"] += batch_size
                    try:
                        sec = float(row.get("seconds", 0) or 0)
                    except (ValueError, TypeError):
                        sec = 0.0
                    total_success_seconds += sec
                    count_success += 1
                elif error_num == 429:
                    metrics["num_429"] += 1
                    try:
                        ra = float(row.get("retry_after", 0) or 0)
                    except (ValueError, TypeError):
                        ra = 0.0
                    total_429_wait += ra
                    count_429 += 1
                else:
                    metrics["num_non_429_errors"] += 1
        else:
            # No header present; interpret columns as:
            # batch_size, seconds, error_code, retry_after
            reader = csv.reader(f)
            for row in reader:
                if not row:
                    continue
                metrics["total_batches"] += 1

                # Ensure row has at least 4 columns
                vals = (row + ["", "", ""])[:4]
                raw_batch, raw_sec, raw_err, raw_retry = vals

                try:
                    batch_size = int(raw_batch or 0)
                except (ValueError, TypeError):
                    batch_size = 0

                try:
                    sec = float(raw_sec or 0)
                except (ValueError, TypeError):
                    sec = 0.0

                error_raw = (raw_err or "").strip()
                error_num = None
                if error_raw != "":
                    try:
                        error_num = int(error_raw)
                    except ValueError:
                        error_num = None

                if error_raw == "" or error_raw == "0":
                    metrics["total_chunks_success"] += batch_size
                    total_success_seconds += sec
                    count_success += 1
                elif error_num == 429:
                    metrics["num_429"] += 1
                    try:
                        ra = float(raw_retry or 0)
                    except (ValueError, TypeError):
                        ra = 0.0
                    total_429_wait += ra
                    count_429 += 1
                else:
                    metrics["num_non_429_errors"] += 1

        if count_429 > 0:
            metrics["avg_429_wait"] = total_429_wait / count_429
        if count_success > 0:
            metrics["avg_latency_success"] = total_success_seconds / count_success

    return metrics


def write_summary_row(summary_path, row_dict):
    """Append a summary row to CSV and return the row index (1-based)."""
    os.makedirs(os.path.dirname(summary_path), exist_ok=True)
    headers = [
        "row",
        "timestamp",
        "celery",
        "duration",
        "non_429s",
        "429s",
        "avg_429_wait",
        "num_docs",
        "avg_L",
        "tpm_k",
    ]

    file_exists = os.path.exists(summary_path)
    current_row = 1
    if file_exists:
        # Count existing data rows to compute next row number
        with open(summary_path, newline="") as f:
            r = csv.reader(f)
            rows = list(r)
            if rows:
                # If header is present, subtract it
                data_rows = rows[1:] if rows[0] and rows[0][0] == "row" else rows
                current_row = len(data_rows) + 1

    row_dict = {**row_dict, "row": current_row}

    # Write header if needed, then append
    with open(summary_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if not file_exists:
            writer.writeheader()
        writer.writerow({k: row_dict.get(k, "") for k in headers})

    return current_row


def pretty_print_ranked_summary(summary_path):
    """Pretty-print the summary CSV ranked by tpm_k (desc)."""
    if not os.path.exists(summary_path):
        print(f"No summary log found at: {summary_path}")
        return

    with open(summary_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    def parse_float(x, default=0.0):
        try:
            return float(x)
        except (ValueError, TypeError):
            return default

    # Sort by tpm_k desc
    rows_sorted = sorted(rows, key=lambda r: parse_float(r.get("tpm_k")), reverse=True)

    # Compute column widths
    cols = [
        ("row", 4),
        ("timestamp", 16),
        ("celery", 6),
        ("duration", 9),
        ("non_429s", 9),
        ("429s", 5),
        ("avg_429_wait", 12),
        ("num_docs", 8),
        ("avg_L", 8),
        ("tpm_k", 8),
    ]

    def fmt(name, width):
        return name.ljust(width)

    # Header
    header = " ".join(fmt(name, width) for name, width in cols)
    print("\nSummary (ranked by tpm_k):")
    print(header)
    print("-" * len(header))

    for r in rows_sorted:
        line = " ".join(
            [
                fmt(str(r.get("row", "")), 4),
                fmt(str(r.get("timestamp", ""))[:16], 16),
                fmt(str(r.get("celery", "")), 6),
                fmt(str(r.get("duration", "")), 9),
                fmt(str(r.get("non_429s", "")), 9),
                fmt(str(r.get("429s", "")), 5),
                fmt(str(r.get("avg_429_wait", "")), 12),
                fmt(str(r.get("num_docs", "")), 8),
                fmt(str(r.get("avg_L", "")), 8),
                fmt(str(r.get("tpm_k", "")), 8),
            ]
        )
        print(line)


def main():
    parser = argparse.ArgumentParser(
        description="Run finalize_document_light timing test"
    )
    parser.add_argument(
        "--num-docs", type=int, default=5, help="Number of documents to create"
    )
    parser.add_argument(
        "--min-chunks", type=int, default=1, help="Min chunks per document"
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
        "--clear-log",
        action="store_true",
        help="Clear the summary log before running the test",
    )
    parser.add_argument(
        "--poll-seconds", type=int, default=5, help="Polling interval in seconds"
    )
    parser.add_argument(
        "--celery", type=int, default=None, help="Celery concurrency (for summary log)"
    )

    args = parser.parse_args()

    log_path = clear_log_file()
    # Ensure directory exists so the task can write
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    # Determine summary log path in the same directory as the embedding log
    summary_log_path = os.path.join(
        os.path.dirname(log_path), "concurrency_experiments.csv"
    )
    if args.clear_log and os.path.exists(summary_log_path):
        os.remove(summary_log_path)
        print(f"Removed old summary log file: {summary_log_path}")

    overall_start = time.time()

    # Queue the documents for processing
    document_ids, _ = fft.queue_test_documents(
        num_documents=args.num_docs,
        min_chunks=args.min_chunks,
        max_chunks=args.max_chunks,
        mock_embedding=args.mock_embedding,
        seed=42,
    )

    # Wait for celery tasks to finish
    if document_ids:
        wait_for_documents(document_ids, poll_seconds=args.poll_seconds)

    overall_duration = time.time() - overall_start

    print(f"\n✓ Test complete. Timing data is in: {log_path}")

    # Parse embedding log and compute metrics
    m = parse_embedding_log(log_path)

    # Compute tokens per minute (k)
    tokens_processed = m["total_chunks_success"] * 750
    tpm = 0.0
    if overall_duration > 0:
        tpm = (tokens_processed / overall_duration) * 60.0
    tpm_k = tpm / 1000.0

    # Write summary row
    # Determine number of documents we attempted (based on queued IDs)
    num_docs = len(document_ids) if document_ids else 0

    summary_row = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "celery": args.celery if args.celery is not None else "",
        "duration": f"{overall_duration:.1f}",
        "non_429s": m["num_non_429_errors"],
        "429s": m["num_429"],
        "avg_429_wait": f"{m['avg_429_wait']:.2f}",
        "num_docs": num_docs,
        "avg_L": f"{m['avg_latency_success']:.2f}",
        "tpm_k": f"{tpm_k:.1f}",
    }
    row_idx = write_summary_row(summary_log_path, summary_row)

    print(f"Summary updated (row {row_idx}) at: {summary_log_path}")
    pretty_print_ranked_summary(summary_log_path)


if __name__ == "__main__":
    main()
