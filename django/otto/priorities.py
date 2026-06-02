# Celery task priorities
# Note that with Redis, there are 4 priority queues by default.
# Priorities 0-2 -> queue 1 (highest)
# Priorities 3-5 -> queue 2
# Priorities 6-8 -> queue 3
# Priority 9    -> queue 4 (lowest)

HIGH = 0
MEDIUM = 3
LOW = 6
LOWEST = 9


def increase_priority(priority, max_priority=HIGH):
    # Use of "max" is counter-intuitive here; lower numerical values indicate higher priority.
    if priority <= MEDIUM:
        return max(HIGH, max_priority)
    if priority <= LOW:
        return max(MEDIUM, max_priority)
    return max(LOW, max_priority)
