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
        try:
            # This URL should always have a table with monthly USD-to-CAD exchange rate data
            bank_site = "https://www.bankofcanada.ca/rates/exchange/daily-exchange-rates-lookup/?lookupPage=lookup_daily_exchange_rates_2017.php&startRange=2017-01-01&series%5B%5D=FXUSDCAD&lookupPage=lookup_daily_exchange_rates_2017.php&startRange=2017-01-01&rangeType=range&rangeValue=1.m&dFrom=2024-11-13&dTo=2024-11-15&submit_button=Submit"
            bank_soup = BeautifulSoup(requests.get(bank_site).content, "html.parser")
            exchange_rate = float(
                re.match(
                    "[\.\d]+",
                    bank_soup.find(id="summary-rates")
                    .find_all("tr")[2] # The row with the month average
                    .find_all("td")[1] # The cell with the actual value
                    .text,
                )[0] # Pull out the first decimal number
            )

            otto_status.exchange_rate = exchange_rate
            otto_status.save()
            self.stdout.write(self.style.SUCCESS(f"Exchange rate updated to {exchange_rate}."))
        except:
            # If the update fails (e.g. if site was down), just retain old value
            self.stdout.write(
                self.style.WARNING(
                    f"Could not update exchange rate. Old value of {otto_status.exchange_rate} retained."
                )
            )
