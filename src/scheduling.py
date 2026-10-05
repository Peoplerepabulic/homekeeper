"""Pure scheduling helpers -- no AWS, no I/O. Easy to unit test."""

from datetime import date, timedelta

DUE_SOON_DAYS = 7


def next_due_date(last_done: date, interval_days: int) -> date:
    """Next due date = last completion + maintenance interval."""
    return last_done + timedelta(days=interval_days)


def urgency(due_date: date, today: date) -> str:
    """Classify a task's urgency: 'overdue' | 'due_soon' | 'ok'."""
    if due_date < today:
        return "overdue"
    if due_date <= today + timedelta(days=DUE_SOON_DAYS):
        return "due_soon"
    return "ok"
