"""Tests for store.py: fuzzy matching, mutations, reorder links, local fallback.

All business logic is tested here (no fastmcp import needed). Each test runs
against a fresh in-memory seed copy with DEMO_CLOCK pinned to 2026-10-04.
"""

from datetime import date, timedelta

import pytest

import store
from store import ApplianceLookupError

T = date(2026, 10, 4)


@pytest.fixture(autouse=True)
def local_seed(monkeypatch):
    monkeypatch.setenv("DEMO_CLOCK", "2026-10-04")
    monkeypatch.delenv("HOMEKEEPER_TABLE", raising=False)
    store.reset_local()
    yield


# --- local fallback -----------------------------------------------------------


def test_fallback_uses_seed_data_when_no_table():
    assert store.is_local()
    appliances = store.get_appliances()
    assert len(appliances) == 8  # the seeded demo household


def test_fallback_triggers_on_dynamo_failure(monkeypatch):
    # HOMEKEEPER_TABLE set but DynamoDB unreachable -> one-way local fallback.
    monkeypatch.setenv("HOMEKEEPER_TABLE", "homekeeper")
    store._backend = None  # force re-resolution
    assert store.is_local() is False
    assert len(store.get_appliances()) == 8  # fell back, no exception
    assert store.is_local() is True  # stays local afterwards


# --- fuzzy matching -------------------------------------------------------------


def test_alias_fridge_matches_both_fridges():
    matches = store.find_appliances("fridge")
    assert {m["appliance_id"] for m in matches} == {"fridge-kitchen", "fridge-garage"}


def test_alias_smoke_detector():
    matches = store.find_appliances("the smoke detector")
    assert {m["appliance_id"] for m in matches} == {"smoke-hallway", "smoke-bedroom"}


def test_phrase_water_heater():
    matches = store.find_appliances("the water heater")
    assert [m["appliance_id"] for m in matches] == ["heater-garage"]


def test_exact_appliance_id():
    matches = store.find_appliances("hvac-main")
    assert [m["appliance_id"] for m in matches] == ["hvac-main"]


def test_no_match_returns_empty():
    assert store.find_appliances("xyz toaster oven") == []


# --- add_appliance -----------------------------------------------------------------


def test_add_appliance_creates_catalog_task_count():
    catalog = store.get_catalog()
    res = store.add_appliance("dishwasher", brand="Bosch", model="SHXM78ZW5N", room="kitchen")
    expected = len(catalog["dishwasher"]["maintenance_tasks"])
    assert len(res["tasks_created"]) == expected == 3
    tasks = store.get_tasks(res["appliance"]["appliance_id"])
    assert len(tasks) == expected


def test_add_appliance_warranty_from_install_date():
    res = store.add_appliance("refrigerator", brand="LG", install_date="2024-01-15")
    expected = (date(2024, 1, 15) + timedelta(days=5 * 365)).isoformat()
    assert res["appliance"]["warranty_end"] == expected


def test_add_appliance_unknown_category_lists_valid():
    with pytest.raises(ValueError, match="Valid categories"):
        store.add_appliance("teleporter")


def test_add_appliance_bad_date():
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        store.add_appliance("dishwasher", install_date="yesterday")


# --- log_maintenance -----------------------------------------------------------------


def test_log_maintenance_done_recomputes():
    res = store.log_maintenance("hvac-main", "air filter", "done")
    assert res["next_due"] == (T + timedelta(days=90)).isoformat()
    assert res["task"]["last_done"] == T.isoformat()
    assert res["task"]["urgency"] == "ok"


def test_log_maintenance_snooze_pushes_30_days():
    # seeded next_due for hvac-main air filter is 2026-09-22
    res = store.log_maintenance("hvac-main", "Replace air filter", "snooze")
    assert res["next_due"] == "2026-10-22"
    assert res["task"]["snoozed"] is True


def test_log_maintenance_bad_action():
    with pytest.raises(ValueError, match="done.*snooze"):
        store.log_maintenance("hvac-main", "air filter", "later")


def test_log_maintenance_ambiguous_appliance_gives_candidates():
    with pytest.raises(ApplianceLookupError) as exc_info:
        store.log_maintenance("hvac", "air filter", "done")
    assert len(exc_info.value.candidates) == 2


def test_log_maintenance_unknown_task_lists_tasks():
    with pytest.raises(ValueError, match="No task matching"):
        store.log_maintenance("hvac-main", "polish the vents", "done")


# --- get_reorder_options -----------------------------------------------------------------


def test_reorder_options_url_format():
    res = store.get_reorder_options("fridge filter")
    assert res["options"], "expected consumable options"
    for opt in res["options"]:
        url = opt["amazon_url"]
        assert url.startswith("https://www.amazon.com/s?k=")
        assert " " not in url, "keywords must be URL-encoded"
    assert "refrigerator+water+filter+cartridge" in res["options"][0]["amazon_url"]


def test_reorder_marks_alert_handled():
    alert_before = next(
        a for a in store.get_alerts() if a["alert_id"] == "recall-fridge-001"
    )
    assert not alert_before.get("reorder_arranged")
    store.get_reorder_options("fridge")
    alert_after = next(
        a for a in store.get_alerts() if a["alert_id"] == "recall-fridge-001"
    )
    assert alert_after.get("reorder_arranged") is True


def test_reorder_no_match():
    res = store.get_reorder_options("left-handed smoke shifter")
    assert res["options"] == []


# --- briefing ------------------------------------------------------------------------------


def test_briefing_counts_match_demo_design():
    b = store.get_briefing()
    items = b["items"]
    assert len(items) == 5
    by_urgency = {}
    for i in items:
        by_urgency.setdefault(i["urgency"], 0)
        by_urgency[i["urgency"]] += 1
    assert by_urgency["overdue"] == 1
    assert by_urgency["due_soon"] == 3  # 2 maintenance + 1 warranty
    assert by_urgency["high"] == 1  # recall
    # sorted: overdue first
    assert items[0]["urgency"] == "overdue"


# --- issue report ----------------------------------------------------------------------------


def test_issue_report_triage_match():
    rep = store.get_issue_report("water heater", "rumbling noise")
    assert rep["triage"]["advice"] == "self_check"
    assert rep["warranty"]["status"] == "active"
    assert "Water Heater" in rep["label"]
    assert rep["device_age"] != "unknown"


def test_issue_report_unknown_symptom_no_crash():
    rep = store.get_issue_report("fridge-kitchen", "it glows purple")
    assert rep["triage"] is None
    assert rep["appliance"]["appliance_id"] == "fridge-kitchen"
