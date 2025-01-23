import os

from django.conf import settings
from django.urls import reverse

import numpy as np
import pytest

from otto.models import Cost, Group, Notification, User


@pytest.mark.django_db
def test_enabling_load_test(client, basic_user, all_apps_user):
    user = basic_user(accept_terms=True)
    client.force_login(user)
    response = client.get(reverse("enable_load_testing"))
    # This shouldn't work
    assert response.status_code == 302
    assert user.notifications.count() == 1
    # Now test with a user that has the correct permissions (all_apps_user)
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("enable_load_testing"))
    assert response.status_code == 200
    # Disable the load test
    response = client.get(reverse("disable_load_testing"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_load_tests(client, all_apps_user):
    # Shouldn't need to be logged in at all
    response = client.get(reverse("load_test"))
    # But since load test isn't enabled, should get a 403
    assert response.status_code == 403
    # Now, enable the load test (as admin user)
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("enable_load_testing"))
    assert response.status_code == 200
    # Logout and try again
    client.logout()
    response = client.get(reverse("load_test"))
    assert response.status_code == 200

    # Try some different load tests to exercise the view
    response = client.get(reverse("load_test"), {"user_library_permissions": ""})
    assert response.status_code == 200
    # response = client.get(reverse("load_test"), {"user_library_permissions": "", "heavy": ""})
    # assert response.status_code == 200
    response = client.get(reverse("load_test"), {"sleep": 1})
    assert response.status_code == 200
    response = client.get(reverse("load_test"), {"error": ""})
    assert response.status_code == 500
    # Should add 1 cost object
    response = client.get(reverse("load_test"), {"query_laws": ""})
    assert response.status_code == 200
    # Should add 2 cost objects
    response = client.get(reverse("load_test"), {"llm_call": ""})
    assert response.status_code == 200
    # Should add 2 cost objects
    response = client.get(reverse("load_test"), {"llm_call": "gpt-4o"})
    assert response.status_code == 200
    # Should add 1 cost object
    response = client.get(reverse("load_test"), {"embed_text": ""})
    assert response.status_code == 200

    # Check that there are 6 cost objects with feature "load_test"
    assert Cost.objects.filter(feature="load_test").count() == 6

    # Login as admin user and disable the load test
    client.force_login(user)
    response = client.get(reverse("disable_load_testing"))
    assert response.status_code == 200
    # Now try to access the load test again
    response = client.get(reverse("load_test"))
    assert response.status_code == 403


@pytest.mark.django_db
def test_celery_load_tests(client, all_apps_user):
    # Enable the load test (as admin user)
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("enable_load_testing"))
    assert response.status_code == 200

    client.logout()

    # Try some different load tests to exercise the view
    response = client.get(reverse("load_test"), {"celery_sleep": 1})
    assert response.status_code == 200
    response = client.get(reverse("load_test"), {"celery_sleep": 1, "show_queue": ""})
    assert response.status_code == 200

    # Login as admin user and disable the load test
    client.force_login(user)
    response = client.get(reverse("disable_load_testing"))
    assert response.status_code == 200
