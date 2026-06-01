from unittest import mock

from otto.celery_diagnostics import (
    collect_celery_snapshot,
    get_priority_bucket_queue_names,
    get_tracked_queue_names,
    serialise_celery_snapshot,
    summarise_celery_snapshot,
)


def test_get_priority_bucket_queue_names_uses_priority_steps():
    assert get_priority_bucket_queue_names("light", priority_steps=[0, 3, 6, 9]) == [
        "light",
        "light\x06\x163",
        "light\x06\x166",
        "light\x06\x169",
    ]


def test_collect_celery_snapshot_groups_workers_and_live_tasks():
    redis_client = mock.Mock()
    redis_client.llen.side_effect = {
        "light": 2,
        "light\x06\x163": 1,
        "light\x06\x166": 0,
        "light\x06\x169": 0,
        "heavy": 1,
        "heavy\x06\x163": 0,
        "heavy\x06\x166": 0,
        "heavy\x06\x169": 0,
    }.get

    inspector = mock.Mock()
    inspector.active_queues.return_value = {
        "celery@light-1": [{"name": "light"}],
        "celery@heavy-1": [{"name": "heavy"}],
    }
    inspector.active.return_value = {
        "celery@light-1": [{"id": "active-light"}],
        "celery@heavy-1": [],
    }
    inspector.reserved.return_value = {
        "celery@light-1": [{"id": "reserved-light"}],
        "celery@heavy-1": [{"id": "reserved-heavy"}],
    }
    inspector.scheduled.return_value = {
        "celery@light-1": [],
        "celery@heavy-1": [{"id": "scheduled-heavy"}],
    }
    inspector.stats.return_value = {}

    celery_app = mock.Mock()
    celery_app.control.inspect.return_value = inspector

    snapshot = collect_celery_snapshot(
        celery_app=celery_app,
        redis_client=redis_client,
        queue_names=["light", "heavy"],
    )
    summary = summarise_celery_snapshot(snapshot)

    assert snapshot["queue_totals"]["light"] == 3
    assert snapshot["queue_totals"]["heavy"] == 1
    assert snapshot["worker_counts"]["light"] == {
        "worker_count": 1,
        "workers": ["celery@light-1"],
        "active": 1,
        "reserved": 1,
        "scheduled": 0,
    }
    assert snapshot["worker_counts"]["heavy"] == {
        "worker_count": 1,
        "workers": ["celery@heavy-1"],
        "active": 0,
        "reserved": 1,
        "scheduled": 1,
    }
    assert snapshot["live_task_ids"] == {
        "active-light",
        "reserved-light",
        "reserved-heavy",
        "scheduled-heavy",
    }
    assert snapshot["inspect_payload"] == {
        "active_queues": {
            "celery@light-1": [{"name": "light"}],
            "celery@heavy-1": [{"name": "heavy"}],
        },
        "active": {
            "celery@light-1": [{"id": "active-light"}],
            "celery@heavy-1": [],
        },
        "reserved": {
            "celery@light-1": [{"id": "reserved-light"}],
            "celery@heavy-1": [{"id": "reserved-heavy"}],
        },
        "scheduled": {
            "celery@light-1": [],
            "celery@heavy-1": [{"id": "scheduled-heavy"}],
        },
        "stats": {},
    }
    assert summary["live_task_count"] == 4


def test_get_tracked_queue_names_uses_embed_name_from_settings():
    assert get_tracked_queue_names() == ["light", "heavy", "embed"]


def test_serialise_celery_snapshot_sorts_live_task_ids_for_json_output():
    snapshot = {
        "queue_names": ["light", "heavy", "embed"],
        "queue_lengths": {"embed": {"embed": 2, "embed\x06\x163": 1}},
        "queue_totals": {"embed": 3},
        "worker_counts": {},
        "live_task_ids": {"task-b", "task-a"},
        "inspect_payload": {"active": {"worker": [{"id": "task-a"}]}},
        "redis_error": None,
        "inspect_error": None,
    }

    assert serialise_celery_snapshot(snapshot) == {
        "queue_names": ["light", "heavy", "embed"],
        "queue_lengths": {"embed": {"embed": 2, "embed\x06\x163": 1}},
        "queue_totals": {"embed": 3},
        "worker_counts": {},
        "live_task_ids": ["task-a", "task-b"],
        "inspect_payload": {"active": {"worker": [{"id": "task-a"}]}},
        "redis_error": None,
        "inspect_error": None,
    }
