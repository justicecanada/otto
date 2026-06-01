from __future__ import annotations

from collections.abc import Iterable, Sequence

from django.conf import settings

import redis

PRIORITY_BUCKET_SEPARATOR = "\x06\x16"


def get_tracked_queue_names(queue_names: Sequence[str] | None = None) -> list[str]:
    return list(
        queue_names
        or [settings.LIGHT_QUEUE, settings.HEAVY_QUEUE, settings.EMBED_QUEUE]
    )


def _get_priority_steps() -> list[int]:
    from otto.celery import app

    steps = app.conf.broker_transport_options.get("priority_steps") or [0]
    return list(steps)


def get_priority_bucket_queue_names(
    queue_name: str, priority_steps: Sequence[int] | None = None
) -> list[str]:
    priority_steps = list(priority_steps or _get_priority_steps())
    queue_keys = [queue_name]
    queue_keys.extend(
        f"{queue_name}{PRIORITY_BUCKET_SEPARATOR}{step}"
        for step in priority_steps
        if step != 0
    )
    return queue_keys


def _count_tasks(
    items_by_worker: dict[str, list[dict]] | None, workers: Iterable[str]
) -> int:
    items_by_worker = items_by_worker or {}
    return sum(len(items_by_worker.get(worker, [])) for worker in workers)


def _extract_task_ids(*task_maps: dict[str, list[dict]] | None) -> set[str]:
    task_ids: set[str] = set()
    for task_map in task_maps:
        for tasks in (task_map or {}).values():
            for task in tasks:
                task_id = task.get("id")
                if task_id:
                    task_ids.add(task_id)
    return task_ids


def group_workers_by_queue(
    active_queues: dict[str, list[dict]] | None,
    queue_names: Sequence[str] | None = None,
) -> dict[str, list[str]]:
    queue_names = get_tracked_queue_names(queue_names)
    grouped = {queue_name: [] for queue_name in queue_names}
    for worker_name, queue_definitions in (active_queues or {}).items():
        configured_queues = {
            queue_definition.get("name")
            for queue_definition in queue_definitions
            if isinstance(queue_definition, dict)
        }
        for queue_name in queue_names:
            if queue_name in configured_queues:
                grouped[queue_name].append(worker_name)
    return grouped


def collect_celery_snapshot(
    *,
    celery_app=None,
    redis_client: redis.Redis | None = None,
    inspect_timeout: float = 1.0,
    queue_names: Sequence[str] | None = None,
) -> dict:
    from otto.celery import app as default_celery_app

    celery_app = celery_app or default_celery_app
    queue_names = get_tracked_queue_names(queue_names)

    queue_lengths: dict[str, dict[str, int]] = {}
    queue_totals: dict[str, int] = {}
    redis_error = None

    try:
        redis_client = redis_client or redis.from_url(settings.REDIS_URL)
        for queue_name in queue_names:
            queue_keys = get_priority_bucket_queue_names(queue_name)
            bucket_lengths = {
                queue_key: int(redis_client.llen(queue_key)) for queue_key in queue_keys
            }
            queue_lengths[queue_name] = bucket_lengths
            queue_totals[queue_name] = sum(bucket_lengths.values())
    except Exception as exc:
        redis_error = str(exc)
        for queue_name in queue_names:
            bucket_lengths = {
                queue_key: 0
                for queue_key in get_priority_bucket_queue_names(queue_name)
            }
            queue_lengths[queue_name] = bucket_lengths
            queue_totals[queue_name] = 0

    worker_counts = {
        queue_name: {
            "worker_count": 0,
            "workers": [],
            "active": 0,
            "reserved": 0,
            "scheduled": 0,
        }
        for queue_name in queue_names
    }
    live_task_ids: set[str] = set()
    inspect_error = None
    inspect_payload = {
        "active_queues": {},
        "active": {},
        "reserved": {},
        "scheduled": {},
        "stats": {},
    }

    try:
        inspector = celery_app.control.inspect(timeout=inspect_timeout)
        active_queues = inspector.active_queues() or {}
        active = inspector.active() or {}
        reserved = inspector.reserved() or {}
        scheduled = inspector.scheduled() or {}
        stats = inspector.stats() or {}
        inspect_payload = {
            "active_queues": active_queues,
            "active": active,
            "reserved": reserved,
            "scheduled": scheduled,
            "stats": stats,
        }

        grouped_workers = group_workers_by_queue(active_queues, queue_names=queue_names)
        for queue_name, workers in grouped_workers.items():
            sorted_workers = sorted(workers)
            worker_counts[queue_name] = {
                "worker_count": len(sorted_workers),
                "workers": sorted_workers,
                "active": _count_tasks(active, sorted_workers),
                "reserved": _count_tasks(reserved, sorted_workers),
                "scheduled": _count_tasks(scheduled, sorted_workers),
            }

        live_task_ids = _extract_task_ids(active, reserved, scheduled)
    except Exception as exc:
        inspect_error = str(exc)

    return {
        "queue_names": queue_names,
        "queue_lengths": queue_lengths,
        "queue_totals": queue_totals,
        "worker_counts": worker_counts,
        "live_task_ids": live_task_ids,
        "inspect_payload": inspect_payload,
        "redis_error": redis_error,
        "inspect_error": inspect_error,
    }


def serialise_celery_snapshot(snapshot: dict) -> dict:
    return {
        **snapshot,
        "queue_names": list(snapshot.get("queue_names") or []),
        "live_task_ids": sorted(snapshot.get("live_task_ids") or []),
        "inspect_payload": dict(snapshot.get("inspect_payload") or {}),
    }


def summarise_celery_snapshot(snapshot: dict) -> dict:
    return {
        "queue_lengths": snapshot["queue_lengths"],
        "queue_totals": snapshot["queue_totals"],
        "worker_counts": snapshot["worker_counts"],
        "live_task_count": len(snapshot.get("live_task_ids", set())),
        "redis_error": snapshot.get("redis_error"),
        "inspect_error": snapshot.get("inspect_error"),
    }
