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
    from otto.models import OttoStatus # Need to do it here to avoid circular imports
    """
    Converts a USD cost to CAD and returns a formatted string
    """
    approx_cost_cad = float(usd_cost) * OttoStatus.objects.singleton().exchange_rate
    if approx_cost_cad < 0.01:
        return "< $0.01"
    return f"${approx_cost_cad:.2f}"


def cad_cost(usd_cost):
    from otto.models import OttoStatus # Need to do it here to avoid circular imports

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
