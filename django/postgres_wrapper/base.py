import errno
import socket

from django.db.backends.postgresql import base

from retrying import retry
from structlog import get_logger

logger = get_logger(__name__)

_TRANSIENT_CONNECTION_ERRNOS = {
    errno.ECONNREFUSED,
    errno.ECONNRESET,
    errno.ETIMEDOUT,
    errno.EHOSTUNREACH,
    errno.ENETUNREACH,
}

_TRANSIENT_CONNECTION_SQLSTATES = {
    "57P03",  # cannot_connect_now
}

_TRANSIENT_CONNECTION_ERROR_TOKENS = (
    "server closed the connection unexpectedly",
    "connection refused",
    "could not connect to server",
    "server login has been failing",
    "server_login_retry",
    "the database system is starting up",
)


def _iter_exception_chain(exc):
    current = exc
    seen = set()

    while current and id(current) not in seen:
        yield current
        seen.add(id(current))
        current = getattr(current, "__cause__", None) or getattr(
            current, "__context__", None
        )


def _is_transient_connection_error(exc):
    if not isinstance(
        exc, (base.Database.OperationalError, base.Database.InterfaceError)
    ):
        return False

    for current_exc in _iter_exception_chain(exc):
        sqlstate = getattr(current_exc, "sqlstate", None) or getattr(
            current_exc, "pgcode", None
        )
        if sqlstate and (
            sqlstate.startswith("08") or sqlstate in _TRANSIENT_CONNECTION_SQLSTATES
        ):
            return True

        error_number = getattr(current_exc, "errno", None)
        if error_number in _TRANSIENT_CONNECTION_ERRNOS:
            return True

        if isinstance(current_exc, socket.gaierror):
            return True

    error_message = str(exc).lower()
    return any(token in error_message for token in _TRANSIENT_CONNECTION_ERROR_TOKENS)


class DatabaseWrapper(base.DatabaseWrapper):
    @retry(
        stop_max_attempt_number=5,
        wait_exponential_multiplier=1000,
        wait_exponential_max=20000,
        retry_on_exception=_is_transient_connection_error,
    )
    def get_new_connection(self, conn_params):
        try:
            return super().get_new_connection(conn_params)
        except Exception as exc:
            if _is_transient_connection_error(exc):
                logger.warning(
                    "postgres_connection_retryable_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            raise
