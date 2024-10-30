import os
import time
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand

from django_extensions.management.utils import signalcommand

from otto.secure_models import AccessKey
from text_extractor.models import OutputFile, UserRequest


class Command(BaseCommand):
    help = "Delete files older than 24 hours"

    @signalcommand
    def handle(self, *args, **kwargs):
        now = datetime.now()
        cutoff = now - timedelta(hours=24)
        access_key = AccessKey(bypass=True)

        # Filter and delete old user requests
        old_requests = UserRequest.objects.filter(
            access_key=access_key, created_at__lt=cutoff
        )
        # print(f"---------------Found {old_requests.count()} user requests to delete")
        for user_request in old_requests:
            user_request.delete(access_key=access_key)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Deleted user request: {user_request.id}, created_at: {user_request.created_at}"
                )
            )
