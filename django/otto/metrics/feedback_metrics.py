from prometheus_client import Counter

otto_feedback_submitted_total = Counter(
    "otto_feedback_submitted_total", "number of feedback submitted", labelnames=["user"]
)
otto_feedback_submitted_with_comment_total = Counter(
    "otto_feedback_submitted_with_comment_total",
    "number of feedback submitted with comment",
    labelnames=["user"],
)
