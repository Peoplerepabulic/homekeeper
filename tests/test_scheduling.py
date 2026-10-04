"""Starter unit tests for the pure scheduling helpers in src/scheduling.py."""

from datetime import date

from scheduling import next_due_date, urgency

# Demo day used across tests (matches .env.example DEMO_CLOCK).
T = date(2026, 10, 4)


def test_next_due_date_adds_interval():
    assert next_due_date(date(2026, 7, 6), 90) == date(2026, 10, 4)


def test_urgency_overdue():
    assert urgency(date(2026, 10, 3), T) == "overdue"


def test_urgency_today_is_due_soon():
    assert urgency(T, T) == "due_soon"


def test_urgency_due_soon_boundary():
    # Exactly DUE_SOON_DAYS (7) out still counts as due soon.
    assert urgency(date(2026, 10, 11), T) == "due_soon"


def test_urgency_ok():
    assert urgency(date(2026, 11, 1), T) == "ok"
