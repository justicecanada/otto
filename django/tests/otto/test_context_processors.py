from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from otto.context_processors import otto_version


def test_otto_version_handles_cache_recursion(rf):
    request = rf.get("/")
    request.user = AnonymousUser()

    with patch(
        "otto.context_processors.cache.get",
        side_effect=[RecursionError(), RecursionError()],
    ):
        context = otto_version(request)

    assert context["load_test_enabled"] is False
    assert "message_from_admins" not in context


@override_settings(DEBUG_TOOLBAR=True)
def test_otto_version_skips_optional_cache_reads_with_debug_toolbar(rf):
    request = rf.get("/")
    request.user = AnonymousUser()

    with patch("otto.context_processors.cache.get") as cache_get:
        context = otto_version(request)

    cache_get.assert_not_called()
    assert context["load_test_enabled"] is False
    assert "message_from_admins" not in context
