import os
from urllib.parse import quote, urlparse

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import redirect

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
    from django.utils.translation import get_language

    from otto.models import OttoStatus  # Need to do it here to avoid circular imports

    """
    Converts a USD cost to CAD and returns a formatted string
    """
    approx_cost_cad = float(usd_cost) * OttoStatus.objects.singleton().exchange_rate
    is_fr = (get_language() or "").lower().startswith("fr")
    if approx_cost_cad < 0.01:
        # For tiny amounts, keep the threshold text but position the $ per locale
        return "< 0.01$" if is_fr else "< $0.01"
    # Position the $ symbol per locale
    return f"{approx_cost_cad:.2f}$" if is_fr else f"${approx_cost_cad:.2f}"


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

    # Ensure the URL starts with https://
    if url.startswith("http://"):
        url = f"https://{url[7:]}"
    if not url.startswith(("https://")):
        return False

    # Extract the domain
    extracted = get_tld_extractor()(urlparse(url).netloc)
    domain = f"{extracted.domain}.{extracted.suffix}"

    # Check if the domain matches or is a subdomain of an allowed domain
    if not any(
        domain == allowed_domain or domain.endswith(f".{allowed_domain}")
        for allowed_domain in settings.ALLOWED_FETCH_URLS
    ):
        BlockedURL.objects.create(url=url)
        return False

    return True


def generate_mailto(to, cc=None, subject="Otto", body=None):
    """
    Generates a mailto link with the provided parameters
    """
    if isinstance(to, list):
        to = ",".join(to)
    if isinstance(cc, list):
        cc = ",".join(cc)
    subject = quote(subject)
    body = quote(body)

    mailto = f"mailto:{to}?subject={subject}"
    if cc:
        mailto += f"&cc={cc}"
    if body:
        mailto += f"&body={body}"
    return mailto


def get_tld_extractor():
    """
    Returns a tldextract.TLDExtract instance with the default suffix list
    """
    return tldextract.TLDExtract(
        suffix_list_urls=[
            "file://" + os.path.join(settings.BASE_DIR, "effective_tld_names.dat")
        ],
        cache_dir=os.path.join(settings.BASE_DIR, "tld_cache"),
    )


def robust_redirect(request, redirect_url):
    """
    Checks if HTMX request and redirects accordingly
    """
    if request.headers.get("HX-Request"):
        response = HttpResponse(status=200)
        response["HX-Redirect"] = redirect_url
        return response
    return redirect(redirect_url)
