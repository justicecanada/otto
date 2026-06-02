import errno
import socket
from unittest.mock import patch

import pytest
import retrying
from postgres_wrapper.base import DatabaseWrapper


class TransientSqlstateError(Exception):
    pgcode = "08006"


class NonTransientSqlstateError(Exception):
    pgcode = "28P01"


@pytest.fixture
def wrapper():
    return DatabaseWrapper.__new__(DatabaseWrapper)


@pytest.fixture(autouse=True)
def skip_retry_sleep(monkeypatch):
    monkeypatch.setattr(retrying.time, "sleep", lambda _: None)


def test_get_new_connection_retries_transient_error_then_succeeds(wrapper):
    transient_error = DatabaseWrapper.Database.OperationalError(
        "server closed the connection unexpectedly"
    )

    with patch(
        "django.db.backends.postgresql.base.DatabaseWrapper.get_new_connection",
        side_effect=[transient_error, object()],
    ) as mock_get_new_connection:
        connection = wrapper.get_new_connection({})

    assert connection is not None
    assert mock_get_new_connection.call_count == 2


def test_get_new_connection_retries_sqlstate_connection_error(wrapper):
    transient_error = DatabaseWrapper.Database.OperationalError("db unavailable")
    transient_error.__cause__ = TransientSqlstateError()

    with patch(
        "django.db.backends.postgresql.base.DatabaseWrapper.get_new_connection",
        side_effect=[transient_error, object()],
    ) as mock_get_new_connection:
        connection = wrapper.get_new_connection({})

    assert connection is not None
    assert mock_get_new_connection.call_count == 2


def test_get_new_connection_retries_errno_from_wrapped_cause(wrapper):
    transient_cause = OSError(errno.ECONNREFUSED, "connection refused")
    transient_error = DatabaseWrapper.Database.InterfaceError("db unavailable")
    transient_error.__cause__ = transient_cause

    with patch(
        "django.db.backends.postgresql.base.DatabaseWrapper.get_new_connection",
        side_effect=[transient_error, object()],
    ) as mock_get_new_connection:
        connection = wrapper.get_new_connection({})

    assert connection is not None
    assert mock_get_new_connection.call_count == 2


def test_get_new_connection_retries_dns_error_from_wrapped_cause(wrapper):
    transient_cause = socket.gaierror(socket.EAI_AGAIN, "temporary failure")
    transient_error = DatabaseWrapper.Database.OperationalError("db unavailable")
    transient_error.__cause__ = transient_cause

    with patch(
        "django.db.backends.postgresql.base.DatabaseWrapper.get_new_connection",
        side_effect=[transient_error, object()],
    ) as mock_get_new_connection:
        connection = wrapper.get_new_connection({})

    assert connection is not None
    assert mock_get_new_connection.call_count == 2


def test_get_new_connection_does_not_retry_non_transient_error(wrapper):
    non_transient_error = DatabaseWrapper.Database.OperationalError(
        "password authentication failed"
    )

    with patch(
        "django.db.backends.postgresql.base.DatabaseWrapper.get_new_connection",
        side_effect=non_transient_error,
    ) as mock_get_new_connection:
        with pytest.raises(DatabaseWrapper.Database.OperationalError):
            wrapper.get_new_connection({})

    assert mock_get_new_connection.call_count == 1


def test_get_new_connection_does_not_retry_non_transient_sqlstate(wrapper):
    non_transient_error = DatabaseWrapper.Database.OperationalError(
        "password authentication failed"
    )
    non_transient_error.__cause__ = NonTransientSqlstateError()

    with patch(
        "django.db.backends.postgresql.base.DatabaseWrapper.get_new_connection",
        side_effect=non_transient_error,
    ) as mock_get_new_connection:
        with pytest.raises(DatabaseWrapper.Database.OperationalError):
            wrapper.get_new_connection({})

    assert mock_get_new_connection.call_count == 1


def test_get_new_connection_raises_after_max_attempts(wrapper):
    transient_error = DatabaseWrapper.Database.InterfaceError(
        "server_login_retry: server login has been failing"
    )

    with patch(
        "django.db.backends.postgresql.base.DatabaseWrapper.get_new_connection",
        side_effect=transient_error,
    ) as mock_get_new_connection:
        with pytest.raises(DatabaseWrapper.Database.InterfaceError):
            wrapper.get_new_connection({})

    assert mock_get_new_connection.call_count == 5
