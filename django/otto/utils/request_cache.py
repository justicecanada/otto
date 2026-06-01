from functools import cache, wraps

from data_fetcher import get_datafetcher_request_cache
from data_fetcher.core import MissingRequestContextException


def quiet_cache_within_request(fn):
    """Cache results for the duration of a request without warning outside requests.

    This mirrors ``data_fetcher.cache_within_request`` except that non-request
    code paths (Celery tasks, management commands, tests, etc.) simply execute
    the wrapped callable normally instead of printing a warning to stdout.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            datafetcher_cache = get_datafetcher_request_cache()
        except MissingRequestContextException:
            return fn(*args, **kwargs)

        if fn not in datafetcher_cache:
            datafetcher_cache[fn] = cache(fn)

        return datafetcher_cache[fn](*args, **kwargs)

    return wrapper
