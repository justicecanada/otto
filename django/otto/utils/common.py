from urllib.parse import urlparse

from django.conf import settings

import tldextract


def file_size_to_string(filesize):
    from django.utils.translation import gettext_lazy as _

    if filesize >= 1024 * 1024:
        return f"{filesize / 1024 / 1024:.2f} {_('MB')}"
    elif filesize >= 1024:
        return f"{filesize / 1024:.2f} {_('KB')}"
    else:
        return f"{filesize} {_('bytes')}"


def display_cad_cost(usd_cost):
    from otto.models import OttoStatus  # Need to do it here to avoid circular imports

    """
    Converts a USD cost to CAD and returns a formatted string
    """
    approx_cost_cad = float(usd_cost) * OttoStatus.objects.singleton().exchange_rate
    if approx_cost_cad < 0.01:
        return "< $0.01"
    return f"${approx_cost_cad:.2f}"


def cad_cost(usd_cost):
    from otto.models import OttoStatus  # Need to do it here to avoid circular imports

    """
    Converts a USD cost to CAD and returns a float
    """
    approx_cost_cad = float(usd_cost) * OttoStatus.objects.singleton().exchange_rate
    return approx_cost_cad


def set_costs(object):
    """
    Sums cost.usd_cost from the object's cost_set and assigns total to object.usd_cost
    """
    object.usd_cost = sum([cost.usd_cost for cost in object.cost_set.all()])
    object.save()


def get_app_from_path(path):
    """
    Returns the app name from a path
    """
    from urllib.parse import urlparse

    parsed_url = urlparse(path)
    path = parsed_url.path.strip("/").split("/")
    # If the path is empty or the result is empty, return "otto"
    if not path or not path[0]:
        return "Otto"
    return path[0]


def check_url_allowed(url):
    from otto.models import BlockedURL

    print("Checking if", url, "is allowed")

    # Ensure the URL starts with https://
    if url.startswith("http://"):
        url = f"https://{url[7:]}"
    if not url.startswith(("https://")):
        print("URL does not start with http:// or https://")
        return False

    # Extract the domain
    extracted = tldextract.extract(urlparse(url).netloc)
    domain = f"{extracted.domain}.{extracted.suffix}"
    print("Extracted domain:", domain)

    # Check if the domain matches or is a subdomain of an allowed domain
    if not any(
        domain == allowed_domain or domain.endswith(f".{allowed_domain}")
        for allowed_domain in settings.ALLOWED_FETCH_URLS
    ):
        print("Domain is not in the allowed list")
        BlockedURL.objects.create(url=url)
        return False

    return True
