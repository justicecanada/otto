from prometheus_client import Counter

# AU-7: Custom metrics that can be used for generating reports on system usage and user interactions

otto_access_total = Counter(
    name="otto_access_total",
    documentation="number of times the otto application pages have been accessed by users",
    labelnames=["user"],
)
