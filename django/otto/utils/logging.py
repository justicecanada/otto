import logging
from typing import Any, Mapping, MutableMapping

from django.dispatch import receiver

import structlog
from django_structlog.signals import bind_extra_request_metadata
from structlog.processors import CallsiteParameter


@receiver(bind_extra_request_metadata)
def bind_username(request, logger, log_kwargs, **kwargs):
    if request.user.is_authenticated:
        structlog.contextvars.bind_contextvars(upn=getattr(request.user, "upn", None))


# Helper formatting function found here: https://github.com/jrobichaud/django-structlog/issues/29
def merge_pathname_lineno_function_to_location(
    logger: structlog.BoundLogger, name: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    pathname = event_dict.pop(CallsiteParameter.PATHNAME.value, None)
    lineno = event_dict.pop(CallsiteParameter.LINENO.value, None)
    func_name = event_dict.pop(CallsiteParameter.FUNC_NAME.value, None)
    event_dict["location"] = f"{pathname}:{lineno}({func_name})"
    return event_dict


class RaiseLevelForEndpointsFilter(logging.Filter):
    IMPORTANT_PATHS = ["/user_cost/", "/notifications/", "/healthz", "/metrics"]

    def filter(self, record):
        msg = getattr(record, "getMessage", lambda: record.msg)()
        # Only allow info-level logs for endpoints NOT in important paths
        # For these paths, pass only WARNING or ERROR and above
        for path in self.IMPORTANT_PATHS:
            if path in msg:
                # Only pass if level is WARNING or higher
                return record.levelno >= logging.WARNING
        return True
