from django.conf import settings


def file_size_to_string(filesize):
    from django.utils.translation import gettext_lazy as _

    if filesize >= 1024 * 1024:
        return f"{filesize / 1024 / 1024:.2f} {_('MB')}"
    elif filesize >= 1024:
        return f"{filesize / 1024:.2f} {_('KB')}"
    else:
        return f"{filesize} {_('bytes')}"


def display_cad_cost(usd_cost):
    """
    Converts a USD cost to CAD and returns a formatted string
    """
    approx_cost_cad = float(usd_cost) * settings.USD_TO_CAD
    if approx_cost_cad < 0.01:
        return "< $0.01"
    return f"${approx_cost_cad:.2f}"


def cad_cost(usd_cost):
    """
    Converts a USD cost to CAD and returns a float
    """
    approx_cost_cad = float(usd_cost) * settings.USD_TO_CAD
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
