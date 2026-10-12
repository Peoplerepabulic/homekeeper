"""Tests for store.py: fuzzy matching, mutations, reorder links, local fallback.

All business logic is tested here (no fastmcp import needed). Each test runs
against a fresh in-memory seed copy with DEMO_CLOCK pinned to 2026-10-04.
"""

from datetime import date, timedelta

import pytest

import store
import tools
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


def test_dynamo_failure_raises_instead_of_faking(monkeypatch):
    monkeypatch.setenv("HOMEKEEPER_TABLE", "homekeeper")
    store._backend = None

    def boom(*a, **k):
        raise RuntimeError("throttled")

    monkeypatch.setattr(store.db, "query_prefix", boom)
    monkeypatch.setattr(store.db, "query_all", boom)
    with pytest.raises(store.StoreUnavailable):
        store.get_appliances()
    assert store.is_local() is False  # never silently switches to seed data
    out = tools.get_home_briefing()
    assert out["status"] == "error" and "items" not in out


class FakeTable:
    """Minimal in-memory stand-in for the db module's DynamoDB calls."""

    def __init__(self, items):
        self.rows = {it["sk"]: dict(it) for it in items}

    def put_item(self, sk, attrs, user_id=None):
        self.rows[sk] = {"sk": sk, **attrs}
        return self.rows[sk]

    def get_item(self, sk, user_id=None):
        return self.rows.get(sk)

    def query_prefix(self, prefix, user_id=None):
        return [dict(v) for k, v in self.rows.items() if k.startswith(prefix)]

    def query_all(self, user_id=None):
        return [dict(v) for v in self.rows.values()]


def test_dynamo_briefing_is_live_after_write(monkeypatch):
    import seed

    fake = FakeTable(seed.build_items())
    monkeypatch.setenv("HOMEKEEPER_TABLE", "homekeeper")
    store._backend = None
    for name in ("put_item", "get_item", "query_prefix", "query_all"):
        monkeypatch.setattr(store.db, name, getattr(fake, name))
    # DynamoDB returns numbers as Decimal; next_due_date must cope.
    from decimal import Decimal

    for row in fake.rows.values():
        if "interval_days" in row:
            row["interval_days"] = Decimal(row["interval_days"])

    before = store.get_briefing()["items"]
    assert any(i["urgency"] == "overdue" for i in before)
    store.log_maintenance("hvac-main", "air filter", "done")
    after = store.get_briefing()["items"]
    assert not any(i["urgency"] == "overdue" for i in after)


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


def test_kitchen_fridge_resolves_to_one():
    assert [m["appliance_id"] for m in store.find_appliances("kitchen fridge")] == ["fridge-kitchen"]


def test_room_alone_stays_ambiguous():
    ids = {m["appliance_id"] for m in store.find_appliances("kitchen")}
    assert ids == {"fridge-kitchen", "purifier-kitchen"}


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


def test_reorder_marks_matching_task_and_briefing_shows_it():
    res = store.get_reorder_options("purifier cartridge")
    assert res["marked_tasks"] == ["Replace filter cartridge"]
    item = next(i for i in store.get_briefing()["items"] if i["task"] == "Replace filter cartridge")
    assert item["reorder_arranged"] is True and "Replacement ordered" in item["detail"]
    store.log_maintenance("purifier-kitchen", "filter cartridge", "done")
    task = next(t for t in store.get_tasks("purifier-kitchen") if t["task"] == "Replace filter cartridge")
    assert "reorder_arranged_on" not in task


def test_reorder_ambiguous_asks_which_and_marks_nothing():
    out = tools.get_reorder_options("fridge filter")
    assert out["status"] == "needs_clarification"
    assert {c["appliance_id"] for c in out["candidates"]} == {"fridge-kitchen", "fridge-garage"}
    assert out["marked_tasks"] == []


def test_recall_alerts_untouched_by_reorder():
    before = [(a["alert_id"], a.get("read")) for a in store.get_alerts()]
    store.get_reorder_options("kitchen fridge filter")
    assert [(a["alert_id"], a.get("read")) for a in store.get_alerts()] == before


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
