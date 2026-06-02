from types import SimpleNamespace
from unittest import mock

import pytest

from chat import responses


@pytest.mark.parametrize(
    "status,has_task_id,expected",
    [
        ("SUCCESS", True, False),
        ("SUCCESS", False, False),
        ("BLOCKED", True, False),
        ("BLOCKED", False, False),
        ("ERROR", True, True),
        ("PENDING", False, True),
        ("INIT", False, True),
        ("PROCESSING", True, False),
        ("PROCESSING", False, True),
        ("TEXT_EXTRACTED", True, False),
        ("TEXT_EXTRACTED", False, True),
    ],
)
def test_should_queue_document_processing(status, has_task_id, expected):
    doc = SimpleNamespace(id=55, status=status)

    with mock.patch(
        "chat.responses.get_celery_task_id",
        return_value="task-1" if has_task_id else None,
    ):
        assert responses._should_queue_document_processing(doc) is expected
