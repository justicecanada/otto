#!/usr/bin/env python
"""
Standalone UAT-like diagnostics for librarian embedding requeue behaviour.

Why this is a standalone script instead of a normal pytest test:
- it starts a real Celery embed worker subprocess
- it may make real embedding API calls and incur cost
- the normal pytest database lifecycle is awkward for cross-process worker tests
- it now cleans up stale `embedding-requeue-*` probe workers before starting
- it aborts if any other worker is subscribed to the `embed` queue, because a shared queue invalidates the repro

Run from `/workspace/django`:

  /usr/local/bin/python tests/misc_non_pytest/embed_requeue_uat.py --mode mock-smoke
  /usr/local/bin/python tests/misc_non_pytest/embed_requeue_uat.py --mode real-repro

Environment overrides:
  EMBED_REQUEUE_WORKER_CONCURRENCY=1
  EMBED_REQUEUE_LARGE_DOC_COUNT=2
  EMBED_REQUEUE_LARGE_CHUNKS=1536
  EMBED_REQUEUE_LARGE_CHUNK_WORDS=1500
  EMBED_REQUEUE_SMALL_CHUNKS=8
  EMBED_REQUEUE_SMALL_CHUNK_WORDS=250
  EMBED_REQUEUE_SMALL_DOC_DELAY_SECONDS=10
  EMBED_REQUEUE_OBSERVE_TIMEOUT_SECONDS=480
  EMBED_REQUEUE_RECOVERY_TIMEOUT_SECONDS=180
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import django
from django.conf import settings

import redis

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "otto.settings")
django.setup()


@dataclass(frozen=True)
class RuntimeDeps:
    celery_app: object
    low_priority: int
    get_celery_task_id: object
    get_embedding_progress: object
    set_celery_task_id: object
    data_source_model: object
    document_model: object
    library_model: object
    finalize_document_light: object


@lru_cache(maxsize=1)
def _runtime() -> RuntimeDeps:
    from otto.celery import app as celery_app
    from otto.priorities import LOW

    from librarian.cache import (
        get_celery_task_id,
        get_embedding_progress,
        set_celery_task_id,
    )
    from librarian.models import DataSource, Document, Library
    from librarian.tasks import finalize_document_light

    return RuntimeDeps(
        celery_app=celery_app,
        low_priority=LOW,
        get_celery_task_id=get_celery_task_id,
        get_embedding_progress=get_embedding_progress,
        set_celery_task_id=set_celery_task_id,
        data_source_model=DataSource,
        document_model=Document,
        library_model=Library,
        finalize_document_light=finalize_document_light,
    )


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _live_embedding_available() -> bool:
    endpoint = getattr(settings, "AZURE_AI_SERVICES_ENDPOINT", None)
    key = getattr(settings, "AZURE_AI_SERVICES_KEY", None)
    return bool(endpoint and key and key != "test-key")


def _build_chunk_payload(words_per_chunk: int, label: str) -> str:
    words = [f"{label}-{idx % 64}" for idx in range(words_per_chunk)]
    return " ".join(words)


def _build_chunks(num_chunks: int, words_per_chunk: int, label: str) -> list[str]:
    payload = _build_chunk_payload(words_per_chunk, label)
    return [payload for _ in range(num_chunks)]


def _queue_lengths() -> dict[str, int]:
    redis_client = redis.from_url(settings.REDIS_URL)
    queue_sep = "\x06\x16"
    queue_names = [
        settings.EMBED_QUEUE,
        f"{settings.EMBED_QUEUE}{queue_sep}3",
        f"{settings.EMBED_QUEUE}{queue_sep}6",
        f"{settings.EMBED_QUEUE}{queue_sep}9",
    ]
    return {
        queue_name: int(redis_client.llen(queue_name)) for queue_name in queue_names
    }


def _snapshot_document(document) -> dict:
    runtime = _runtime()
    document.refresh_from_db()
    progress = runtime.get_embedding_progress(document.id) or {}
    return {
        "document_id": document.id,
        "status": document.status,
        "task_id": runtime.get_celery_task_id(document.id),
        "next_index": progress.get("next_index"),
        "stuck_counter": progress.get("stuck_counter"),
        "num_chunks": document.num_chunks,
        "status_details": document.status_details,
        "requeue_reason": progress.get("requeue_reason"),
        "requeue_countdown_seconds": progress.get("requeue_countdown_seconds"),
    }


def _write_trace(trace_name: str, payload: dict) -> str:
    trace_dir = Path(settings.MEDIA_ROOT) / "embedding_requeue_traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_path = trace_dir / f"{trace_name}_{uuid.uuid4().hex}.json"
    trace_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    return str(trace_path)


def _queue_finalize(document, chunks: list[str], mock_embedding: bool) -> str:
    runtime = _runtime()
    result = runtime.finalize_document_light.apply_async(
        kwargs={
            "document_id": document.id,
            "chunks": chunks,
            "mock_embedding": mock_embedding,
        },
        priority=runtime.low_priority,
    )
    runtime.set_celery_task_id(document.id, result.id)
    return result.id


def _wait_for_terminal_status(document, timeout_seconds: int) -> dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        snapshot = _snapshot_document(document)
        if snapshot["status"] in {"SUCCESS", "ERROR", "BLOCKED", "PAUSED"}:
            return snapshot
        time.sleep(2)
    return _snapshot_document(document)


def _document_started_embedding(snapshot: dict) -> bool:
    return snapshot.get("num_chunks") not in {None, 0} or snapshot.get(
        "next_index"
    ) not in {None, 0}


def _large_doc_continuation_started_before_small(
    history: dict[int, list[dict]],
) -> bool:
    for snapshots in history.values():
        initial_task_id = None
        for idx, snapshot in enumerate(snapshots):
            task_id = snapshot.get("task_id")
            if not task_id:
                continue
            if initial_task_id is None:
                initial_task_id = task_id
                continue
            if task_id == initial_task_id:
                continue

            continuation_start_index = snapshot.get("next_index")
            for later_snapshot in snapshots[idx + 1 :]:
                if later_snapshot.get("task_id") != task_id:
                    continue
                later_index = later_snapshot.get("next_index")
                if (
                    continuation_start_index is not None
                    and later_index is not None
                    and later_index > continuation_start_index
                ):
                    return True
            break
    return False


def _get_or_create_probe_library():
    library, _ = _runtime().library_model.objects.get_or_create(
        name="Embedding Requeue Investigation"
    )
    return library


def _embed_queue_workers() -> set[str]:
    active_queues = _runtime().celery_app.control.inspect(timeout=2.0).active_queues()
    if not active_queues:
        return set()

    workers = set()
    for hostname, queues in active_queues.items():
        if any(queue.get("name") == settings.EMBED_QUEUE for queue in queues or []):
            workers.add(hostname)
    return workers


def _shutdown_workers(hostnames: set[str]):
    if not hostnames:
        return
    _runtime().celery_app.control.broadcast(
        "shutdown",
        destination=sorted(hostnames),
    )


def _cleanup_stale_probe_workers():
    stale_workers = {
        hostname
        for hostname in _embed_queue_workers()
        if hostname.startswith("embedding-requeue-")
    }
    if not stale_workers:
        return

    _shutdown_workers(stale_workers)
    deadline = time.time() + 20
    while time.time() < deadline:
        remaining_workers = _embed_queue_workers() & stale_workers
        if not remaining_workers:
            return
        time.sleep(1)

    raise RuntimeError(
        "Failed to shut down stale probe workers before starting a new repro: "
        f"{sorted(_embed_queue_workers() & stale_workers)}"
    )


def _assert_embed_queue_isolated(allowed_workers: set[str]):
    extra_workers = _embed_queue_workers() - allowed_workers
    if extra_workers:
        raise RuntimeError(
            "Embed queue is not isolated for this repro. Other workers are subscribed: "
            f"{sorted(extra_workers)}"
        )


@dataclass
class RunningEmbedWorker:
    concurrency: int = 1
    prefetch_multiplier: int = 1
    process: subprocess.Popen | None = None
    log_path: str | None = None
    hostname: str | None = None

    def __enter__(self) -> "RunningEmbedWorker":
        _cleanup_stale_probe_workers()

        worker_id = uuid.uuid4().hex[:8]
        self.hostname = f"embedding-requeue-{worker_id}@{socket.gethostname()}"
        worker_dir = Path(settings.MEDIA_ROOT) / "embedding_requeue_traces"
        worker_dir.mkdir(parents=True, exist_ok=True)
        log_path = worker_dir / f"embedworker_{worker_id}.log"

        env = os.environ.copy()
        env.setdefault("DJANGO_SETTINGS_MODULE", "otto.settings")
        env["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        env["CELERY_POOL"] = "gevent"
        env.setdefault("PYTHONUNBUFFERED", "1")

        log_handle = open(log_path, "w", encoding="utf-8", buffering=1)
        self.process = subprocess.Popen(
            [
                "celery",
                "-A",
                "otto",
                "worker",
                "-l",
                "INFO",
                "--pool=gevent",
                "--concurrency",
                str(self.concurrency),
                "-Q",
                settings.EMBED_QUEUE,
                "--prefetch-multiplier",
                str(self.prefetch_multiplier),
                "--hostname",
                self.hostname,
            ],
            cwd=os.getcwd(),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        self.log_path = str(log_path)

        deadline = time.time() + 60
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"Embed worker exited before becoming ready. Log: {self.log_path}"
                )
            try:
                ping = (
                    _runtime()
                    .celery_app.control.inspect(
                        timeout=1.0, destination=[self.hostname]
                    )
                    .ping()
                )
                if ping and self.hostname in ping:
                    _assert_embed_queue_isolated({self.hostname})
                    return self
            except Exception:
                pass
            time.sleep(1)

        raise RuntimeError(
            f"Timed out waiting for embed worker readiness. Log: {self.log_path}"
        )

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.process is None:
            return False
        if self.hostname:
            try:
                _shutdown_workers({self.hostname})
            except Exception:
                pass
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        return False


def run_mock_smoke() -> int:
    runtime = _runtime()
    library = _get_or_create_probe_library()
    data_source = runtime.data_source_model.objects.create(
        library=library,
        name=f"Embedding smoke {uuid.uuid4().hex[:8]}",
    )
    document = runtime.document_model.objects.create(
        data_source=data_source,
        filename="embedworker-mock-smoke.txt",
        status="TEXT_EXTRACTED",
        url_content_type="text/plain",
    )
    chunks = _build_chunks(num_chunks=96, words_per_chunk=300, label="smoke")

    with RunningEmbedWorker(
        concurrency=_env_int("EMBED_REQUEUE_WORKER_CONCURRENCY", 1),
        prefetch_multiplier=1,
    ) as worker:
        task_id = _queue_finalize(document, chunks, mock_embedding=True)
        print(f"Queued mock smoke document {document.id} with task {task_id}")
        final_snapshot = _wait_for_terminal_status(document, timeout_seconds=180)
        trace_path = _write_trace(
            "embed_requeue_mock_smoke",
            {
                "worker_log_path": worker.log_path,
                "document": final_snapshot,
                "queue_lengths": _queue_lengths(),
            },
        )
        print(f"Trace written to: {trace_path}")
        if final_snapshot["status"] != "SUCCESS":
            print(json.dumps(final_snapshot, indent=2, sort_keys=True))
            return 1
        return 0


def run_real_repro() -> int:
    if not _live_embedding_available():
        print("Real embedding configuration is not available.")
        return 2

    runtime = _runtime()
    library = _get_or_create_probe_library()
    data_source = runtime.data_source_model.objects.create(
        library=library,
        name=f"Embedding requeue repro {uuid.uuid4().hex[:8]}",
    )

    large_doc_count = _env_int("EMBED_REQUEUE_LARGE_DOC_COUNT", 2)
    large_chunk_count = _env_int("EMBED_REQUEUE_LARGE_CHUNKS", 1536)
    large_chunk_words = _env_int("EMBED_REQUEUE_LARGE_CHUNK_WORDS", 1500)
    small_chunk_count = _env_int("EMBED_REQUEUE_SMALL_CHUNKS", 8)
    small_chunk_words = _env_int("EMBED_REQUEUE_SMALL_CHUNK_WORDS", 250)
    small_doc_delay_seconds = _env_int("EMBED_REQUEUE_SMALL_DOC_DELAY_SECONDS", 10)
    observe_timeout_seconds = _env_int("EMBED_REQUEUE_OBSERVE_TIMEOUT_SECONDS", 480)
    recovery_timeout_seconds = _env_int("EMBED_REQUEUE_RECOVERY_TIMEOUT_SECONDS", 180)

    large_chunks = _build_chunks(large_chunk_count, large_chunk_words, label="large")
    small_chunks = _build_chunks(small_chunk_count, small_chunk_words, label="small")

    large_documents = [
        runtime.document_model.objects.create(
            data_source=data_source,
            filename=f"large-{idx}.txt",
            status="TEXT_EXTRACTED",
            url_content_type="text/plain",
        )
        for idx in range(large_doc_count)
    ]
    small_document = runtime.document_model.objects.create(
        data_source=data_source,
        filename="small.txt",
        status="TEXT_EXTRACTED",
        url_content_type="text/plain",
    )

    trace = {
        "config": {
            "large_doc_count": large_doc_count,
            "large_chunk_count": large_chunk_count,
            "large_chunk_words": large_chunk_words,
            "small_chunk_count": small_chunk_count,
            "small_chunk_words": small_chunk_words,
            "small_doc_delay_seconds": small_doc_delay_seconds,
            "observe_timeout_seconds": observe_timeout_seconds,
            "recovery_timeout_seconds": recovery_timeout_seconds,
            "worker_concurrency": _env_int("EMBED_REQUEUE_WORKER_CONCURRENCY", 1),
            "chunk_embedding_parallel_requests": os.environ.get(
                "CHUNK_EMBEDDING_PARALLEL_REQUESTS"
            ),
        },
        "events": [],
        "worker_log_path": None,
    }
    history = {document.id: [] for document in [*large_documents, small_document]}

    with RunningEmbedWorker(
        concurrency=_env_int("EMBED_REQUEUE_WORKER_CONCURRENCY", 1),
        prefetch_multiplier=1,
    ) as worker:
        trace["worker_log_path"] = worker.log_path

        for document in large_documents:
            task_id = _queue_finalize(document, large_chunks, mock_embedding=False)
            trace["events"].append(
                {
                    "event": "queued_large",
                    "document_id": document.id,
                    "task_id": task_id,
                    "timestamp": time.time(),
                }
            )

        time.sleep(small_doc_delay_seconds)
        small_task_id = _queue_finalize(
            small_document, small_chunks, mock_embedding=False
        )
        trace["events"].append(
            {
                "event": "queued_small",
                "document_id": small_document.id,
                "task_id": small_task_id,
                "timestamp": time.time(),
            }
        )

        start_time = time.time()
        starvation_detected = False
        while time.time() - start_time < observe_timeout_seconds:
            snapshots = {}
            for document in [*large_documents, small_document]:
                snapshot = _snapshot_document(document)
                history[document.id].append(snapshot)
                snapshots[str(document.id)] = snapshot

            trace["events"].append(
                {
                    "event": "poll",
                    "elapsed_seconds": round(time.time() - start_time, 2),
                    "queue_lengths": _queue_lengths(),
                    "snapshots": snapshots,
                }
            )

            small_snapshot = snapshots[str(small_document.id)]
            if _document_started_embedding(small_snapshot):
                trace["events"].append(
                    {
                        "event": "small_started",
                        "elapsed_seconds": round(time.time() - start_time, 2),
                        "snapshot": small_snapshot,
                    }
                )
                trace_path = _write_trace("embed_requeue_post_fix_ok", trace)
                print(
                    "Small document started before any large-document continuation overtook it. "
                    f"Trace: {trace_path}; worker log: {worker.log_path}"
                )
                return 0

            if _large_doc_continuation_started_before_small(
                {doc.id: history[doc.id] for doc in large_documents}
            ):
                starvation_detected = True
                trace["events"].append(
                    {
                        "event": "starvation_detected",
                        "elapsed_seconds": round(time.time() - start_time, 2),
                        "reason": "large_continuation_started_before_small",
                    }
                )
                break

            time.sleep(2)

        if not starvation_detected:
            trace_path = _write_trace("embed_requeue_no_repro", trace)
            print(
                "Did not observe either a small-document start or a large-continuation leapfrog event "
                f"within {observe_timeout_seconds}s. Trace: {trace_path}; worker log: {worker.log_path}"
            )
            return 1

        for document in large_documents:
            document.stop()
            trace["events"].append(
                {
                    "event": "stopped_large",
                    "document_id": document.id,
                    "timestamp": time.time(),
                }
            )

        recovery_deadline = time.time() + recovery_timeout_seconds
        small_recovered = False
        while time.time() < recovery_deadline:
            snapshot = _snapshot_document(small_document)
            history[small_document.id].append(snapshot)
            trace["events"].append(
                {
                    "event": "recovery_poll",
                    "timestamp": time.time(),
                    "queue_lengths": _queue_lengths(),
                    "snapshot": snapshot,
                }
            )
            if snapshot["status"] == "SUCCESS":
                small_recovered = True
                break
            time.sleep(2)

    trace_path = _write_trace("embed_requeue_repro", trace)
    if not small_recovered:
        print(
            "Observed apparent starvation, but the small document did not recover after stopping the large documents. "
            f"Trace: {trace_path}; worker log: {trace['worker_log_path']}"
        )
        return 1

    print(f"Reproduction trace written to: {trace_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="UAT-like embedding requeue diagnostics"
    )
    parser.add_argument(
        "--mode",
        choices=["mock-smoke", "real-repro"],
        required=True,
        help="Which diagnostic mode to run.",
    )
    args = parser.parse_args()

    if args.mode == "mock-smoke":
        return run_mock_smoke()
    return run_real_repro()


if __name__ == "__main__":
    raise SystemExit(main())
