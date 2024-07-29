from prometheus_client import Counter

template_wizard_access_total = Counter(
    name="template_wizard_access_total",
    documentation="number of times the template wizard has been accessed by users",
    labelnames=["user"],
)
