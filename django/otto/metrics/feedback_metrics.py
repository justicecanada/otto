from prometheus_client import Counter

# AU-7: Custom metrics that can be used for generating reports on system usage and user interactions

otto_feedback_submitted_total = Counter(
    "otto_feedback_submitted_total", "number of feedback submitted", labelnames=["user"]
)
otto_feedback_submitted_with_comment_total = Counter(
    "otto_feedback_submitted_with_comment_total",
    "number of feedback submitted with comment",
    labelnames=["user"],
)
