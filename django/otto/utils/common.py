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
    approx_cost_cad = float(usd_cost) * settings.USD_TO_CAD
    if approx_cost_cad < 0.005:
        return "< $0.01"
    return f"${approx_cost_cad:.2f}"
