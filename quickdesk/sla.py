"""QuickDesk SLA rules and calculation helpers."""

from datetime import datetime, timedelta


SLA_HOURS = {
    "urgent": 4,
    "high": 8,
    "medium": 24,
    "low": 72,
}

PRIORITY_LABELS = {
    "urgent": "紧急",
    "high": "高",
    "medium": "中",
    "low": "低",
}

ALLOWED_PRIORITIES = frozenset(SLA_HOURS)


def normalize_priority(priority, fallback="medium"):
    """Return a valid priority or the supplied fallback."""
    if priority in ALLOWED_PRIORITIES:
        return priority

    if fallback in ALLOWED_PRIORITIES:
        return fallback

    return "medium"


def calculate_sla_deadline(priority, start_time=None):
    """Calculate and format an SLA deadline for a priority."""
    normalized_priority = normalize_priority(priority)
    start_time = start_time or datetime.now()

    deadline = start_time + timedelta(
        hours=SLA_HOURS[normalized_priority]
    )

    return deadline.strftime("%Y-%m-%d %H:%M:%S")


def get_sla_hours(priority):
    """Return the configured SLA hours for a priority."""
    normalized_priority = normalize_priority(priority)
    return SLA_HOURS[normalized_priority]


def get_priority_label(priority):
    """Return the Chinese display label for a priority."""
    normalized_priority = normalize_priority(priority)
    return PRIORITY_LABELS[normalized_priority]