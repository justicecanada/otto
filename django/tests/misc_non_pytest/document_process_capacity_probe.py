#!/usr/bin/env python
"""
Interactive operator probe for librarian document processing and embedding capacity.

This script creates many synthetic text documents of different sizes, queues them
through the real `Document.process()` path, and watches them flow through the
heavy -> embed pipeline. It is designed for manual execution inside a pod shell
(e.g. `kubectl exec -it ... -- bash`) in DEV or STG.

Why this exists:
- benchmark_embedding_latency.py measures raw embedding API throughput only
- this probe measures the more production-like end-to-end path:
  SavedFile -> Document.process() -> process_document -> finalize_document_light
- it produces a clear recommendation block for environment variable tuning

Typical usage from `/workspace/django`:

  python tests/misc_non_pytest/document_process_capacity_probe.py
  python tests/misc_non_pytest/document_process_capacity_probe.py --preset balanced --yes
  python tests/misc_non_pytest/document_process_capacity_probe.py --preset heavy --poll-seconds 15 --timeout-seconds 7200
  python tests/misc_non_pytest/document_process_capacity_probe.py --preset quick --mock-embedding --cleanup --yes

Notes:
- This script performs real embedding calls unless `--mock-embedding` is passed.
- Default presets stay under the librarian auto-embed pause threshold where possible.
- It writes a JSON trace under MEDIA_ROOT for later analysis.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import django
from django.conf import settings

import redis

# Assume the script is run from the django directory inside the container/pod.
sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "otto.settings")
django.setup()


@dataclass(frozen=True)
class SizeProfile:
    label: str
    target_chunks: int
    count: int


@dataclass(frozen=True)
class ProbePreset:
    name: str
    description: str
    profiles: tuple[SizeProfile, ...]


PRESETS: dict[str, ProbePreset] = {
    "quick": ProbePreset(
        name="quick",
        description="Fast smoke test with dozens of docs across 3 sizes.",
        profiles=(
            SizeProfile("medium", 36, 18),
            SizeProfile("large", 72, 12),
            SizeProfile("xlarge", 192, 6),
        ),
    ),
    "balanced": ProbePreset(
        name="balanced",
        description="General-use STG/DEV probe with a more prod-like size distribution.",
        profiles=(
            SizeProfile("medium", 40, 80),
            SizeProfile("large", 72, 70),
            SizeProfile("xlarge", 128, 35),
            SizeProfile("huge", 384, 15),
            SizeProfile("jumbo", 1200, 10),
        ),
    ),
    "heavy": ProbePreset(
        name="heavy",
        description="Aggressive probe that builds substantial heavy/embed backlog.",
        profiles=(
            SizeProfile("medium", 40, 110),
            SizeProfile("large", 72, 90),
            SizeProfile("xlarge", 128, 55),
            SizeProfile("huge", 384, 30),
            SizeProfile("jumbo", 1200, 15),
        ),
    ),
}

TERMINAL_STATUSES = {"SUCCESS", "ERROR", "BLOCKED"}
QUEUE_SEP = "\x06\x16"


@lru_cache(maxsize=1)
def _runtime():
    from otto.celery import app as celery_app
    from otto.models import OttoStatus
    from otto.priorities import LOW

    from librarian.cache import get_celery_task_id, get_embedding_progress
    from librarian.models import DataSource, Document, Library
    from librarian.utils.process_document import save_content_to_saved_file
    from librarian.utils.process_engine import (
        get_process_engine_from_type,
        split_markdown_into_chunks,
    )

    return {
        "celery_app": celery_app,
        "otto_status_model": OttoStatus,
        "low_priority": LOW,
        "library_model": Library,
        "data_source_model": DataSource,
        "document_model": Document,
        "save_content_to_saved_file": save_content_to_saved_file,
        "split_markdown_into_chunks": split_markdown_into_chunks,
        "get_process_engine_from_type": get_process_engine_from_type,
        "get_celery_task_id": get_celery_task_id,
        "get_embedding_progress": get_embedding_progress,
    }


def _env_int(name: str, default: int | None = None) -> int | None:
    raw = os.environ.get(name)
    if raw in {None, ""}:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _prompt_choice(prompt: str, options: list[str], default: str) -> str:
    rendered = "/".join([f"[{o}]" if o == default else o for o in options])
    while True:
        raw = input(f"{prompt} {rendered}: ").strip().lower()
        if not raw:
            return default
        if raw in options:
            return raw
        print(f"Please choose one of: {', '.join(options)}")


def _prompt_bool(prompt: str, default: bool) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input(f"{prompt} {suffix}: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def _prompt_int(
    prompt: str, default: int | None = None, allow_blank: bool = False
) -> int | None:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw:
            if default is not None:
                return default
            if allow_blank:
                return None
        try:
            return int(raw)
        except ValueError:
            print("Please enter an integer value.")


def _choose_preset(args) -> ProbePreset:
    if args.preset:
        return PRESETS[args.preset]
    choice = _prompt_choice(
        "Choose probe preset",
        list(PRESETS.keys()),
        default="balanced",
    )
    return PRESETS[choice]


def _environment_label(args) -> str:
    if args.environment_label:
        return args.environment_label
    detected = (
        os.environ.get("ENV")
        or os.environ.get("ENVIRONMENT")
        or os.environ.get("OTTO_ENV")
        or "unknown"
    )
    if args.yes:
        return detected
    raw = input(f"Environment label [{detected}]: ").strip()
    return raw or detected


def _current_worker_snapshot() -> dict:
    app = _runtime()["celery_app"]
    inspect = app.control.inspect(timeout=2.0)
    active_queues = inspect.active_queues() or {}
    stats = inspect.stats() or {}

    by_queue: dict[str, list[dict]] = defaultdict(list)
    for hostname, queues in active_queues.items():
        pool = stats.get(hostname, {}).get("pool", {})
        concurrency = pool.get("max-concurrency") or pool.get("writes")
        for queue in queues or []:
            by_queue[queue.get("name", "unknown")].append(
                {
                    "hostname": hostname,
                    "concurrency": concurrency,
                }
            )
    return dict(by_queue)


def _infer_embed_concurrency_from_workers() -> int | None:
    workers = _current_worker_snapshot().get(settings.EMBED_QUEUE, [])
    if not workers:
        return None
    values = []
    for worker in workers:
        concurrency = worker.get("concurrency")
        if isinstance(concurrency, int):
            values.append(concurrency)
        else:
            try:
                values.append(int(concurrency))
            except (TypeError, ValueError):
                continue
    return sum(values) if values else None


def _queue_lengths() -> dict[str, int]:
    redis_client = redis.from_url(settings.REDIS_URL)
    totals: dict[str, int] = {}
    for queue_name in [
        settings.HEAVY_QUEUE,
        settings.LIGHT_QUEUE,
        settings.EMBED_QUEUE,
    ]:
        names = [
            queue_name,
            f"{queue_name}{QUEUE_SEP}3",
            f"{queue_name}{QUEUE_SEP}6",
            f"{queue_name}{QUEUE_SEP}9",
        ]
        totals[queue_name] = sum(int(redis_client.llen(name)) for name in names)
    return totals


def _paragraph(label: str, paragraph_index: int, words_per_paragraph: int = 120) -> str:
    words = [
        f"{label}-{paragraph_index}-{idx % 97}" for idx in range(words_per_paragraph)
    ]
    return f"Section {paragraph_index}\n\n" + " ".join(words)


@lru_cache(maxsize=None)
def _payload_for_target_chunks(target_chunks: int) -> tuple[str, int, int]:
    runtime = _runtime()
    splitter = runtime["split_markdown_into_chunks"]
    process_engine = runtime["get_process_engine_from_type"]("text/plain")

    def build(paragraph_count: int) -> str:
        return "\n\n".join(_paragraph("probe", i) for i in range(paragraph_count))

    def chunk_count(paragraph_count: int) -> int:
        markdown = build(paragraph_count)
        return len(splitter(markdown, process_engine=process_engine))

    low, high = 1, 1
    while chunk_count(high) < target_chunks:
        low = high
        high *= 2

    while low + 1 < high:
        mid = (low + high) // 2
        if chunk_count(mid) >= target_chunks:
            high = mid
        else:
            low = mid

    markdown = build(high)
    actual_chunks = len(splitter(markdown, process_engine=process_engine))
    return markdown, actual_chunks, high


def _create_probe_library(environment_label: str):
    runtime = _runtime()
    library = runtime["library_model"].objects.create(
        name=f"Embedding Capacity Probe ({environment_label}) {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    data_source = runtime["data_source_model"].objects.create(
        library=library,
        name="Synthetic probe documents",
    )
    return library, data_source


def _create_document(data_source, label: str, body: str, sequence: int):
    runtime = _runtime()
    unique = uuid.uuid4().hex[:10]
    filename = f"embedding_probe_{label}_{sequence:04d}_{unique}.txt"
    content = (
        f"Embedding capacity probe\n"
        f"Label: {label}\n"
        f"Sequence: {sequence}\n"
        f"Unique: {unique}\n\n"
        f"{body}"
    ).encode("utf-8")

    saved_file, resolved_name, _ = runtime["save_content_to_saved_file"](
        content,
        filename=filename,
        content_type="text/plain",
    )
    document = runtime["document_model"].objects.create(
        data_source=data_source,
        saved_file=saved_file,
        filename=resolved_name,
        provenance=runtime["document_model"].PROVENANCE_GENERATED_OUTPUT,
    )
    return document


def _status_snapshot(document_ids: list[int]) -> dict:
    runtime = _runtime()
    documents = list(
        runtime["document_model"]
        .objects.filter(id__in=document_ids)
        .only(
            "id",
            "status",
            "num_chunks",
            "status_details",
            "created_at",
            "fetched_at",
        )
    )
    counts = Counter(doc.status for doc in documents)
    success_chunks = sum(
        (doc.num_chunks or 0) for doc in documents if doc.status == "SUCCESS"
    )
    paused_chunks = sum(
        (doc.num_chunks or 0) for doc in documents if doc.status == "PAUSED"
    )
    error_details = Counter(
        (doc.status_details or "").strip()[:200] or "(no status_details)"
        for doc in documents
        if doc.status in {"ERROR", "BLOCKED"}
    )
    progress_reasons = Counter()
    waiting_reasons = Counter()
    for doc in documents:
        progress = runtime["get_embedding_progress"](doc.id) or {}
        requeue_reason = progress.get("requeue_reason")
        if requeue_reason:
            progress_reasons[requeue_reason] += 1

        if doc.status in TERMINAL_STATUSES or doc.status == "PAUSED":
            continue

        initial_status_text = str(progress.get("initial_status_text") or "")
        countdown_seconds = progress.get("requeue_countdown_seconds")
        waiting_for_continuation = (
            bool(requeue_reason)
            and " - waiting)" in initial_status_text
            and (
                countdown_seconds is None or isinstance(countdown_seconds, (int, float))
            )
        )
        if waiting_for_continuation:
            waiting_reasons[requeue_reason] += 1

    return {
        "counts": dict(counts),
        "terminal": sum(counts.get(status, 0) for status in TERMINAL_STATUSES),
        "success_chunks": success_chunks,
        "paused_chunks": paused_chunks,
        "paused_document_ids": [doc.id for doc in documents if doc.status == "PAUSED"],
        "error_details": dict(error_details.most_common(5)),
        "requeue_reasons": dict(progress_reasons),
        "waiting_continuations": sum(waiting_reasons.values()),
        "waiting_reasons": dict(waiting_reasons),
    }


def _resume_paused_documents(
    paused_document_ids: list[int],
    resumed_document_ids: set[int],
    *,
    mock_embedding: bool,
    priority: int,
) -> list[int]:
    if not paused_document_ids:
        return []

    runtime = _runtime()
    resumed_now: list[int] = []
    for document in runtime["document_model"].objects.filter(
        id__in=paused_document_ids
    ):
        if document.id in resumed_document_ids:
            continue
        result = document.start_manual_embedding(
            mock_embedding=mock_embedding,
            priority=priority,
        )
        if result is not None:
            resumed_document_ids.add(document.id)
            resumed_now.append(document.id)
    return resumed_now


def _print_worker_snapshot():
    workers = _current_worker_snapshot()
    print("\nCurrent worker subscriptions:")
    for queue_name in [
        settings.HEAVY_QUEUE,
        settings.LIGHT_QUEUE,
        settings.EMBED_QUEUE,
    ]:
        entries = workers.get(queue_name, [])
        if not entries:
            print(f"  {queue_name}: no visible workers")
            continue
        details = ", ".join(
            f"{entry['hostname']} (concurrency={entry['concurrency']})"
            for entry in entries
        )
        print(f"  {queue_name}: {details}")


def _print_plan(
    environment_label: str, preset: ProbePreset, actual_chunks: dict[str, int]
):
    print("\nProbe plan")
    print("-" * 72)
    print(f"Environment:                {environment_label}")
    print(f"Preset:                     {preset.name} — {preset.description}")
    print(
        f"Current embed concurrency:  {os.environ.get('CELERY_EMBEDDINGWORKER_CONCURRENCY', '(unset)')}"
    )
    print(
        f"Embedding batch size:       {getattr(settings, 'EMBEDDING_BATCH_SIZE', '(unknown)')}"
    )
    print(
        f"Embed time slice seconds:   {getattr(settings, 'EMBED_TIME_SLICE_SECONDS', '(unknown)')}"
    )
    print("Approx chunk tokens:        750")

    threshold = (
        _runtime()["otto_status_model"]
        .objects.singleton()
        .librarian_auto_embed_max_chunks
    )
    print(f"Auto-embed pause threshold: {threshold}")
    print("Paused probe docs auto-resume: yes")
    print("\nDocument mix:")
    for profile in preset.profiles:
        print(
            f"  - {profile.label:<7} target={profile.target_chunks:<4} actual≈{actual_chunks[profile.label]:<4} count={profile.count}"
        )


def _write_trace(payload: dict) -> str:
    trace_dir = Path(settings.MEDIA_ROOT) / "embedding_capacity_probe"
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_path = trace_dir / f"document_process_capacity_probe_{uuid.uuid4().hex}.json"
    trace_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    return str(trace_path)


def _embed_log_path() -> str:
    return getattr(
        settings,
        "LIBRARIAN_EMBEDDING_LOG_PATH",
        os.path.join(settings.MEDIA_ROOT, "librarian_embedding_log.csv"),
    )


def _embed_log_row_count(log_path: str) -> int:
    if not os.path.exists(log_path):
        return 0
    try:
        with open(log_path, newline="", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _parse_embed_log_delta(log_path: str, start_row_count: int) -> dict:
    summary = {
        "available": False,
        "new_rows": 0,
        "success_batches": 0,
        "error_429": 0,
        "other_errors": 0,
        "avg_retry_after_seconds": 0.0,
    }
    if not os.path.exists(log_path):
        return summary

    try:
        with open(log_path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
    except OSError:
        return summary

    summary["available"] = True
    if not rows:
        return summary

    data_rows = rows[1:] if rows and rows[0][:2] == ["batch_size", "seconds"] else rows
    if start_row_count > 0:
        header_adjustment = (
            1 if rows and rows[0][:2] == ["batch_size", "seconds"] else 0
        )
        skip_data_rows = max(0, start_row_count - header_adjustment)
        data_rows = data_rows[skip_data_rows:]

    retry_afters = []
    for row in data_rows:
        if not row:
            continue
        summary["new_rows"] += 1
        error_code = (row[2] if len(row) > 2 else "").strip()
        retry_after = (row[3] if len(row) > 3 else "").strip()
        if error_code in {"", "0"}:
            summary["success_batches"] += 1
        elif error_code == "429":
            summary["error_429"] += 1
            try:
                retry_afters.append(float(retry_after))
            except (TypeError, ValueError):
                pass
        else:
            summary["other_errors"] += 1

    if retry_afters:
        summary["avg_retry_after_seconds"] = sum(retry_afters) / len(retry_afters)
    return summary


def _estimate_quota_tpm() -> int | None:
    direct = _env_int("TEXT_EMBEDDING_3_LARGE_CAPACITY_TPM")
    if direct:
        return direct
    cap = _env_int("TEXT_EMBEDDING_3_LARGE_CAPACITY")
    if cap:
        return cap * 1000
    return None


def _resolve_runtime_inputs(args) -> tuple[int | None, int | None]:
    embed_concurrency = getattr(args, "embed_concurrency", None)
    if embed_concurrency is None:
        embed_concurrency = _env_int("CELERY_EMBEDDINGWORKER_CONCURRENCY")
    if embed_concurrency is None:
        embed_concurrency = _infer_embed_concurrency_from_workers()

    quota_tpm = getattr(args, "embedding_quota_tpm", None)
    if quota_tpm is None:
        quota_tpm = _estimate_quota_tpm()

    if args.yes:
        return embed_concurrency, quota_tpm

    embed_concurrency = _prompt_int(
        "Current embed-worker concurrency",
        default=embed_concurrency,
        allow_blank=embed_concurrency is None,
    )

    quota_tpm = _prompt_int(
        "Azure embedding quota (TPM) for indexing",
        default=quota_tpm,
        allow_blank=quota_tpm is None,
    )

    return embed_concurrency, quota_tpm


def _recommend(summary: dict) -> dict:
    current_concurrency = summary.get("input_embed_concurrency")
    quota_tpm = summary.get("input_quota_tpm")
    observed_tpm = summary.get("estimated_tpm", 0)
    completion_ratio = summary.get("terminal_docs", 0) / max(
        summary.get("total_docs", 1), 1
    )
    error_docs = summary.get("counts", {}).get("ERROR", 0) + summary.get(
        "counts", {}
    ).get("BLOCKED", 0)
    embed_log = summary.get("embed_log", {})
    error_429 = int(embed_log.get("error_429", 0) or 0)
    embed_rows = int(embed_log.get("new_rows", 0) or 0)
    avg_retry_after = float(embed_log.get("avg_retry_after_seconds", 0.0) or 0.0)
    requeue_events = int(summary.get("observed_requeue_events", 0) or 0)
    error_429_rate = (error_429 / embed_rows) if embed_rows else 0.0

    recommendation = {
        "current_embed_concurrency": current_concurrency,
        "quota_tpm": quota_tpm,
        "target_index_tpm": None,
        "recommended_embed_concurrency": current_concurrency,
        "next_test_concurrency": current_concurrency,
        "next_test_strategy": "hold",
        "confidence": "low",
        "reason": "Insufficient data to recommend a change.",
        "next_test_reason": "Insufficient data to recommend a follow-up probe step.",
        "action": "unknown",
    }

    if quota_tpm:
        recommendation["target_index_tpm"] = int(quota_tpm)

    if not current_concurrency or not quota_tpm or observed_tpm <= 0:
        return recommendation

    target_index_tpm = recommendation["target_index_tpm"]

    if error_docs > 0:
        reduced = max(1, int(round(current_concurrency * 0.8)))
        recommendation.update(
            {
                "recommended_embed_concurrency": reduced,
                "next_test_concurrency": reduced,
                "next_test_strategy": "stabilize",
                "confidence": "medium" if completion_ratio >= 0.9 else "low",
                "action": "change",
                "reason": (
                    "Observed terminal ERROR/BLOCKED documents. Reduce concurrency first, "
                    "retest, then scale back up more cautiously."
                ),
                "next_test_reason": (
                    "Use the safer reduced setting for the very next run before doing any broader search."
                ),
            }
        )
        return recommendation

    if error_429_rate >= 0.05 or avg_retry_after >= 5.0:
        quota_aligned = max(
            1, int(round(current_concurrency * target_index_tpm / observed_tpm))
        )
        binary_step = max(1, current_concurrency // 2)

        if error_429_rate >= 0.30 or avg_retry_after >= 10.0:
            severity_backoff = max(1, int(round(current_concurrency * 0.75)))
        elif error_429_rate >= 0.15 or avg_retry_after >= 7.5:
            severity_backoff = max(1, int(round(current_concurrency * 0.82)))
        else:
            severity_backoff = max(1, int(round(current_concurrency * 0.9)))

        if observed_tpm > target_index_tpm * 1.05:
            reduced = min(severity_backoff, quota_aligned)
            reason = (
                "Embedding batch logs recorded materially frequent 429s, and observed TPM is already above the "
                "dedicated indexing quota. Reduce concurrency toward the quota-aligned level, then re-run and "
                "compare throughput and 429 rate."
            )
        else:
            reduced = severity_backoff
            reason = (
                "Embedding batch logs recorded materially frequent 429s or long Retry-After values. "
                "Back off concurrency, then re-run and compare throughput."
            )

        if error_429_rate >= 0.15 or avg_retry_after >= 5.0:
            next_test_concurrency = binary_step
            next_test_strategy = "binary-search"
            next_test_reason = (
                "For rapid operator testing, halve concurrency first. If 429s remain material, continue searching below "
                "that point; if 429s drop and TPM falls below quota, search upward between the halved setting and the "
                "current run."
            )
        else:
            next_test_concurrency = reduced
            next_test_strategy = "confirm-steady-state"
            next_test_reason = "The 429s are material but not extreme; the next run can test the proposed steady-state setting directly."

        recommendation.update(
            {
                "recommended_embed_concurrency": reduced,
                "next_test_concurrency": next_test_concurrency,
                "next_test_strategy": next_test_strategy,
                "confidence": "high" if completion_ratio >= 0.98 else "medium",
                "action": "change",
                "reason": reason,
                "next_test_reason": next_test_reason,
            }
        )
        return recommendation

    if requeue_events > max(10, summary.get("total_docs", 0) * 0.25):
        reduced = max(1, int(round(current_concurrency * 0.95)))
        recommendation.update(
            {
                "recommended_embed_concurrency": reduced,
                "next_test_concurrency": reduced,
                "next_test_strategy": "confirm-steady-state",
                "confidence": "medium",
                "action": "change",
                "reason": (
                    "Observed frequent continuation requeues. Current concurrency is close to the "
                    "limit; trim it slightly unless there is still significant unused dedicated indexing TPM."
                ),
                "next_test_reason": (
                    "Use the slightly reduced setting for the next run and confirm whether requeues and throughput improve."
                ),
            }
        )
        return recommendation

    raw_scaled = current_concurrency * target_index_tpm / observed_tpm
    if observed_tpm >= target_index_tpm and error_429_rate <= 0.02:
        recommendation.update(
            {
                "recommended_embed_concurrency": current_concurrency,
                "next_test_concurrency": current_concurrency,
                "next_test_strategy": "hold",
                "confidence": "high" if completion_ratio >= 0.98 else "medium",
                "action": "keep",
                "reason": (
                    "Observed TPM already makes strong use of the dedicated indexing quota, and 429s are absent "
                    "or minimal. Prefer the higher-throughput setting unless 429 frequency becomes material."
                ),
                "next_test_reason": (
                    "No follow-up search is needed unless you want to probe slightly higher for curiosity."
                ),
            }
        )
        return recommendation

    if raw_scaled > current_concurrency:
        scaled = max(current_concurrency + 1, int(round(raw_scaled * 0.9)))
        reason = (
            "Observed TPM is below the dedicated indexing quota; scale concurrency up "
            "with a 10% safety haircut."
        )
    else:
        scaled = max(1, int(round(raw_scaled)))
        reason = (
            "Observed TPM already meets or exceeds the dedicated indexing quota; "
            "current concurrency is likely sufficient or can be reduced slightly."
        )

    recommendation.update(
        {
            "recommended_embed_concurrency": scaled,
            "next_test_concurrency": scaled,
            "next_test_strategy": "confirm-steady-state",
            "confidence": "high" if completion_ratio >= 0.98 else "medium",
            "action": "keep" if scaled == current_concurrency else "change",
            "reason": reason,
            "next_test_reason": (
                "Use the proposed setting for the next run to confirm the quota-aligned estimate."
            ),
        }
    )

    return recommendation


def _print_recommendation(summary: dict, recommendation: dict):
    print("\nRecommendation")
    print("=" * 72)
    print(f"Observed estimated TPM:        {summary['estimated_tpm']:,}")
    if recommendation.get("quota_tpm"):
        print(f"Configured embedding quota:    {recommendation['quota_tpm']:,}")
    if recommendation.get("target_index_tpm"):
        print(f"Target indexing TPM (100%):    {recommendation['target_index_tpm']:,}")
    print(
        f"Current env concurrency:       {recommendation['current_embed_concurrency']}"
    )
    print(
        f"Suggested concurrency:         {recommendation['recommended_embed_concurrency']}"
    )
    print(
        f"Suggested next test:           {recommendation['next_test_concurrency']} ({recommendation['next_test_strategy']})"
    )
    print(f"Confidence:                    {recommendation['confidence']}")
    embed_log = summary.get("embed_log", {})
    print(
        f"Observed continuation requeues:{summary.get('observed_requeue_events', 0):>9}"
    )
    print(
        f"Peak waiting continuations:     {summary.get('peak_waiting_continuations', 0)}"
    )
    if embed_log.get("available"):
        print(f"Observed embed-batch 429s:     {embed_log.get('error_429', 0)}")
        print(f"Observed other embed errors:   {embed_log.get('other_errors', 0)}")
        if embed_log.get("new_rows"):
            rate = embed_log.get("error_429", 0) / embed_log.get("new_rows", 1)
            print(f"Observed 429 rate:             {rate:.2%}")
        if embed_log.get("error_429"):
            print(
                f"Avg Retry-After (429s):        {embed_log.get('avg_retry_after_seconds', 0.0):.2f}s"
            )
    else:
        print(
            "Observed embed-batch 429s:     unavailable (embed CSV logging not present in this environment)"
        )
    print()
    if recommendation.get("action") == "keep":
        print("I recommend keeping these environment variables at:")
    else:
        print("I recommend changing these specific environment variables to:")
    print(
        f"  CELERY_EMBEDDINGWORKER_CONCURRENCY={recommendation['recommended_embed_concurrency']}"
    )
    print()
    print(f"Rationale: {recommendation['reason']}")
    print(f"Next test: {recommendation['next_test_reason']}")


def parse_args():
    parser = argparse.ArgumentParser(description="Run document-process capacity probe")
    parser.add_argument("--preset", choices=sorted(PRESETS.keys()))
    parser.add_argument("--environment-label")
    parser.add_argument("--embedding-quota-tpm", type=int)
    parser.add_argument("--embed-concurrency", type=int)
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--mock-embedding", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Run non-interactively")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    preset = _choose_preset(args)
    environment_label = _environment_label(args)
    input_embed_concurrency, input_quota_tpm = _resolve_runtime_inputs(args)
    random.seed(args.seed)

    actual_chunks: dict[str, int] = {}
    paragraph_counts: dict[str, int] = {}
    threshold = (
        _runtime()["otto_status_model"]
        .objects.singleton()
        .librarian_auto_embed_max_chunks
    )
    for profile in preset.profiles:
        _, chunk_count, paragraph_count = _payload_for_target_chunks(
            profile.target_chunks
        )
        actual_chunks[profile.label] = chunk_count
        paragraph_counts[profile.label] = paragraph_count

    _print_worker_snapshot()
    _print_plan(environment_label, preset, actual_chunks)
    if input_embed_concurrency is None:
        print(
            "Current embed concurrency:     (not detected; recommendation confidence will be lower)"
        )
    else:
        print(f"Resolved embed concurrency:    {input_embed_concurrency}")
    if input_quota_tpm is None:
        print(
            "Resolved embedding quota TPM:  (not detected; recommendation confidence will be lower)"
        )
    else:
        print(f"Resolved embedding quota TPM:  {input_quota_tpm:,}")

    if threshold is not None:
        over_threshold = [
            profile.label
            for profile in preset.profiles
            if actual_chunks[profile.label] > threshold
        ]
        if over_threshold:
            print(
                "\nNote: these profiles exceed the auto-embed pause threshold and will be auto-resumed by the probe:\n"
                f"  {', '.join(over_threshold)}"
            )

    if not args.yes and not _prompt_bool("Proceed with probe?", default=False):
        print("Aborted.")
        return 1

    start_ts = time.time()
    embed_log_path = _embed_log_path()
    embed_log_start_rows = _embed_log_row_count(embed_log_path)
    library, data_source = _create_probe_library(environment_label)
    payloads = {
        profile.label: _payload_for_target_chunks(profile.target_chunks)[0]
        for profile in preset.profiles
    }

    work_items: list[tuple[str, int]] = []
    for profile in preset.profiles:
        work_items.extend((profile.label, idx + 1) for idx in range(profile.count))
    random.shuffle(work_items)

    document_ids: list[int] = []
    created_documents: list[dict] = []
    last_task_ids: dict[int, str | None] = {}
    seen_task_ids: dict[int, list[str]] = defaultdict(list)
    observed_requeue_events = 0
    observed_requeue_reasons = Counter()
    resumed_paused_documents: set[int] = set()
    peak_waiting_continuations = 0
    peak_waiting_reasons = Counter()
    print("\nCreating and queueing documents...")
    for index, (label, sequence) in enumerate(work_items, start=1):
        document = _create_document(data_source, label, payloads[label], index)
        document.process(
            mock_embedding=args.mock_embedding, priority=_runtime()["low_priority"]
        )
        document_ids.append(document.id)
        initial_task_id = _runtime()["get_celery_task_id"](document.id)
        last_task_ids[document.id] = initial_task_id
        if initial_task_id:
            seen_task_ids[document.id].append(initial_task_id)
        created_documents.append(
            {
                "document_id": document.id,
                "label": label,
                "queued_index": index,
                "target_chunks": next(
                    p.target_chunks for p in preset.profiles if p.label == label
                ),
                "actual_chunks_preview": actual_chunks[label],
            }
        )
        if index == len(work_items) or index % 25 == 0:
            print(f"  queued {index}/{len(work_items)} documents")

    timeline: list[dict] = []
    print("\nWatching progress...")
    while True:
        snapshot = _status_snapshot(document_ids)
        resumed_now = _resume_paused_documents(
            snapshot.get("paused_document_ids", []),
            resumed_paused_documents,
            mock_embedding=args.mock_embedding,
            priority=_runtime()["low_priority"],
        )
        if resumed_now:
            print(
                "Auto-resumed paused documents for embedding: "
                + ", ".join(str(document_id) for document_id in resumed_now)
            )
            snapshot = _status_snapshot(document_ids)

        for document_id in document_ids:
            task_id = _runtime()["get_celery_task_id"](document_id)
            if task_id and task_id not in seen_task_ids[document_id]:
                seen_task_ids[document_id].append(task_id)
                # First distinct task id = heavy worker, second = initial embed task.
                # Count only third+ task ids as real embed continuations.
                if len(seen_task_ids[document_id]) >= 3:
                    observed_requeue_events += 1
                    progress = _runtime()["get_embedding_progress"](document_id) or {}
                    observed_requeue_reasons[
                        progress.get("requeue_reason") or "unspecified_continuation"
                    ] += 1
            if task_id:
                last_task_ids[document_id] = task_id
        queue_lengths = _queue_lengths()
        current_waiting_continuations = snapshot.get("waiting_continuations", 0)
        if current_waiting_continuations > peak_waiting_continuations:
            peak_waiting_continuations = current_waiting_continuations
            peak_waiting_reasons = Counter(snapshot.get("waiting_reasons", {}))
        elapsed = int(time.time() - start_ts)
        line = (
            f"T+{elapsed:>4}s | terminal={snapshot['terminal']:>4}/{len(document_ids)}"
            f" success={snapshot['counts'].get('SUCCESS', 0):>4}"
            f" paused={snapshot['counts'].get('PAUSED', 0):>3}"
            f" error={snapshot['counts'].get('ERROR', 0) + snapshot['counts'].get('BLOCKED', 0):>3}"
            f" processing={snapshot['counts'].get('PROCESSING', 0):>4}"
            f" waiting={snapshot.get('waiting_continuations', 0):>4}"
            f" extracted={snapshot['counts'].get('TEXT_EXTRACTED', 0):>4}"
            f" | q heavy={queue_lengths.get(settings.HEAVY_QUEUE, 0):>4}"
            f" embed={queue_lengths.get(settings.EMBED_QUEUE, 0):>4}"
            f" light={queue_lengths.get(settings.LIGHT_QUEUE, 0):>4}"
        )
        print(line)
        timeline.append(
            {
                "elapsed_seconds": elapsed,
                "snapshot": snapshot,
                "queue_lengths": queue_lengths,
            }
        )

        if snapshot["terminal"] >= len(document_ids):
            break
        if time.time() - start_ts >= args.timeout_seconds:
            print("\nTimeout reached before all documents reached terminal status.")
            break
        time.sleep(args.poll_seconds)

    total_duration = time.time() - start_ts
    final_snapshot = _status_snapshot(document_ids)
    embed_log_summary = _parse_embed_log_delta(embed_log_path, embed_log_start_rows)
    success_docs = final_snapshot["counts"].get("SUCCESS", 0)
    total_docs = len(document_ids)
    estimated_tpm = (
        int((final_snapshot["success_chunks"] * 750) / (total_duration / 60))
        if total_duration > 0
        else 0
    )

    recommendation = _recommend(
        {
            "input_embed_concurrency": input_embed_concurrency,
            "input_quota_tpm": input_quota_tpm,
            "estimated_tpm": estimated_tpm,
            "terminal_docs": final_snapshot["terminal"],
            "total_docs": total_docs,
            "counts": final_snapshot["counts"],
            "observed_requeue_events": observed_requeue_events,
            "peak_waiting_continuations": peak_waiting_continuations,
            "embed_log": embed_log_summary,
        }
    )

    summary = {
        "environment_label": environment_label,
        "preset": preset.name,
        "duration_seconds": round(total_duration, 2),
        "total_docs": total_docs,
        "terminal_docs": final_snapshot["terminal"],
        "counts": final_snapshot["counts"],
        "success_chunks": final_snapshot["success_chunks"],
        "paused_chunks": final_snapshot["paused_chunks"],
        "estimated_tpm": estimated_tpm,
        "input_embed_concurrency": input_embed_concurrency,
        "input_quota_tpm": input_quota_tpm,
        "observed_requeue_events": observed_requeue_events,
        "observed_requeue_reasons": dict(observed_requeue_reasons),
        "peak_waiting_continuations": peak_waiting_continuations,
        "peak_waiting_reasons": dict(peak_waiting_reasons),
        "resumed_paused_documents": sorted(resumed_paused_documents),
        "embed_log": embed_log_summary,
        "queue_lengths_end": _queue_lengths(),
        "error_details": final_snapshot["error_details"],
        "requeue_reasons": final_snapshot["requeue_reasons"],
        "recommendation": recommendation,
    }

    trace_payload = {
        "config": {
            "environment_label": environment_label,
            "preset": preset.name,
            "preset_profiles": [asdict(profile) for profile in preset.profiles],
            "poll_seconds": args.poll_seconds,
            "timeout_seconds": args.timeout_seconds,
            "mock_embedding": args.mock_embedding,
            "cleanup": args.cleanup,
            "seed": args.seed,
        },
        "env": {
            "CELERY_EMBEDDINGWORKER_CONCURRENCY": os.environ.get(
                "CELERY_EMBEDDINGWORKER_CONCURRENCY"
            ),
            "CHUNK_EMBEDDING_PARALLEL_REQUESTS": os.environ.get(
                "CHUNK_EMBEDDING_PARALLEL_REQUESTS"
            ),
            "TEXT_EMBEDDING_3_LARGE_CAPACITY": os.environ.get(
                "TEXT_EMBEDDING_3_LARGE_CAPACITY"
            ),
            "TEXT_EMBEDDING_3_LARGE_CAPACITY_TPM": os.environ.get(
                "TEXT_EMBEDDING_3_LARGE_CAPACITY_TPM"
            ),
            "EMBEDDING_BATCH_SIZE": getattr(settings, "EMBEDDING_BATCH_SIZE", None),
            "EMBED_TIME_SLICE_SECONDS": getattr(
                settings, "EMBED_TIME_SLICE_SECONDS", None
            ),
            "librarian_auto_embed_max_chunks": threshold,
            "embed_log_path": embed_log_path,
        },
        "payload_previews": {
            label: {
                "actual_chunks": actual_chunks[label],
                "paragraph_count": paragraph_counts[label],
            }
            for label in actual_chunks
        },
        "library_id": library.id,
        "data_source_id": data_source.id,
        "documents": created_documents,
        "timeline": timeline,
        "summary": summary,
    }
    trace_path = _write_trace(trace_payload)

    print("\nFinal summary")
    print("=" * 72)
    print(f"Library id:                     {library.id}")
    print(f"Data source id:                 {data_source.id}")
    print(f"Documents queued:               {total_docs}")
    print(f"Duration:                       {total_duration:.1f}s")
    print(f"Terminal documents:             {final_snapshot['terminal']}/{total_docs}")
    print(f"Success documents:              {success_docs}")
    print(
        f"Paused documents remaining:     {final_snapshot['counts'].get('PAUSED', 0)}"
    )
    print(f"Paused docs auto-resumed:       {len(resumed_paused_documents)}")
    print(
        f"Error/blocked documents:        {final_snapshot['counts'].get('ERROR', 0) + final_snapshot['counts'].get('BLOCKED', 0)}"
    )
    print(f"Successful chunks:              {final_snapshot['success_chunks']:,}")
    print(f"Estimated TPM:                  {estimated_tpm:,}")
    print(f"Observed continuation requeues: {observed_requeue_events}")
    print(f"Peak waiting continuations:     {peak_waiting_continuations}")
    if observed_requeue_reasons:
        print(f"Observed requeue reasons:       {dict(observed_requeue_reasons)}")
    if peak_waiting_reasons:
        print(f"Peak waiting reasons:          {dict(peak_waiting_reasons)}")
    if embed_log_summary.get("available"):
        print(f"Embed log rows observed:        {embed_log_summary.get('new_rows', 0)}")
        print(
            f"Embed log 429s:                 {embed_log_summary.get('error_429', 0)}"
        )
        print(
            f"Embed log other errors:         {embed_log_summary.get('other_errors', 0)}"
        )
        if embed_log_summary.get("error_429"):
            print(
                f"Avg Retry-After:                {embed_log_summary.get('avg_retry_after_seconds', 0.0):.2f}s"
            )
    if final_snapshot["requeue_reasons"]:
        print(f"Observed requeue reasons:       {final_snapshot['requeue_reasons']}")
    if final_snapshot["error_details"]:
        print(f"Top error details:              {final_snapshot['error_details']}")
    print(f"Trace written to:               {trace_path}")

    _print_recommendation(summary, recommendation)

    if args.cleanup:
        print("\nCleaning up probe library...")
        library.delete()
        print("Cleanup queued.")
    else:
        print(
            "\nProbe data was kept for inspection. To clean it up later, delete library "
            f"{library.id} / data source {data_source.id}."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
