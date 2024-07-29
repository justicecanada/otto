from prometheus_client import Counter

otto_access_total = Counter(
    name="otto_access_total",
    documentation="number of times the otto application pages have been accessed by users",
    labelnames=["user"],
)
