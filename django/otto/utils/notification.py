from django.conf import settings

import requests


def send_via_gc_notify(data):
    api_key = settings.GC_NOTIFY_KEY
    url = "https://api.notification.canada.ca/v2/notifications/email"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"ApiKey-v1 {api_key}",
    }

    response = requests.post(url, json=data, headers=headers)
    return response
