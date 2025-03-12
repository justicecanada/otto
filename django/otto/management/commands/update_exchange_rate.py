import os
import re
import time
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand

import requests
from bs4 import BeautifulSoup
from django_extensions.management.utils import signalcommand

from otto.models import OttoStatus


class Command(BaseCommand):
    @signalcommand
    def handle(self, *args, **kwargs):
        otto_status = OttoStatus.objects.singleton()
        # This URL should always have a table with monthly USD-to-CAD exchange rate data
        bank_site = "https://www.bankofcanada.ca/rates/exchange/monthly-exchange-rates/"
        bank_soup = BeautifulSoup(requests.get(bank_site).content, "html.parser")
        exchange_rate = float(
            bank_soup.find("th", text=re.compile("US dollar"))
            .find_parent("tr")
            .find_all("td")[-1]
            .text
        )

        otto_status.exchange_rate = exchange_rate
        otto_status.save()
        self.stdout.write(
            self.style.SUCCESS(f"Exchange rate updated to {exchange_rate}.")
        )
