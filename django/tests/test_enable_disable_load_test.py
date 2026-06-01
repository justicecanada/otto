from django.core.cache import cache
from django.core.management import call_command

import pytest


@pytest.mark.django_db
def test_enable_load_test_sets_cache_flag():
    cache.set("load_testing_enabled", False)
    call_command("enable_load_test", duration=5)
    assert cache.get("load_testing_enabled") is True


@pytest.mark.django_db
def test_disable_load_test_clears_cache_flag():
    cache.set("load_testing_enabled", True)
    call_command("disable_load_test")
    assert cache.get("load_testing_enabled") is False
