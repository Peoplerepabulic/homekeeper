"""Tests for the cold-path agent task functions (fully offline).

The real ``src.store`` module is used with ``reset_local()`` (in-memory seed
copy — no DynamoDB, no credentials). CPSC HTTP is never hit: ``fetch_recalls``
is monkeypatched with fixture data.
"""

import json
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))

import home_analyst as ha
import db
import store


@pytest.fixture(autouse=True)
def local_store(monkeypatch):
    monkeypatch.setenv("DEMO_CLOCK", "2026-10-04")
    store.reset_local()
    ha._CPSC_CACHE["recalls"] = []
    ha._CPSC_CACHE["at"] = 0.0
    yield


# --- fixture recalls (shape mirrors the real CPSC API) ---------------------------


def _recall(rid, title, description, brand_note=""):
    return {
        "RecallID": rid,
        "RecallNumber": 26000 + rid % 1000,
        "RecallDate": "2026-09-01T00:00:00",
        "LastPublishDate": "2026-09-02T00:00:00",
        "Title": title,
        "Description": description,
        "URL": f"https://cpsc.gov/Recalls/2026/fixture-{rid}",
        "Products": [{"Name": f"{brand_note} product", "Description": "",
                      "Model": "", "Type": "", "CategoryID": ""}],
        "Hazards": [{"Name": "Test hazard."}],
        "Injuries": [{"Name": "None reported"}],
        "Manufacturers": [], "Retailers": [], "Importers": [],
        "Distributors": [], "Inconjunctions": [], "ProductUPCs": [],
        "Images": [], "ManufacturerCountries": [],
        "Remedies": [{"Name": "Free repair"}],
        "RemedyOptions": [{"Option": "Repair"}],
        "ConsumerContact": "1-800-000-0000",
        "SoldAtLabel": None,
    }


FIXTURE_RECALLS = [
    _recall(99991,
            "Samsung Recalls RF28R7351SR French Door Refrigerators Due to Fire Hazard",
            "This recall involves Samsung model RF28R7351SR French-door "
            "refrigerators with flex-zone drawers.",
            "Samsung Refrigerator"),
    _recall(99992,
            "Acme Recalls X1 Widget Chargers Due to Burn Hazard",
            "This recall involves Acme model X1 USB wall chargers.",
            "Acme Charger"),
]


# --- match_recalls ---------------------------------------------------------------


def test_match_requires_brand_and_model():
    assert [r["RecallID"] for r in ha.match_recalls(FIXTURE_RECALLS, "Samsung", "RF28R7351SR")] == [99991]
    assert ha.match_recalls(FIXTURE_RECALLS, "Samsung", "RF28R0000XX") == []  # model mismatch
    assert ha.match_recalls(FIXTURE_RECALLS, "LG", "RF28R7351SR") == []  # brand mismatch
    assert ha.match_recalls(FIXTURE_RECALLS, "Samsung", "") == []
    assert ha.match_recalls(FIXTURE_RECALLS, "", "RF28R7351SR") == []
    assert ha.match_recalls(FIXTURE_RECALLS, None, None) == []


def test_match_normalizes_punctuation_and_case():
    rec = _recall(99993, "5Color Recalls YD-001 Helmets", "Model No.: YD-001, pink.", "5Color")
    assert ha.match_recalls([rec], "5-Color", "YD 001") == [rec]
    assert ha.match_recalls([rec], "samsung", "yd-001") == []


def test_cpsc_search_tool_shape(monkeypatch):
    monkeypatch.setattr(ha, "fetch_recalls", lambda timeout=90: FIXTURE_RECALLS)
    out = json.loads(ha.cpsc_search("samsung", "rf28r7351sr"))
    assert len(out) == 1
    assert out[0]["recall_id"] == 99991
    assert out[0]["url"].startswith("https://cpsc.gov/Recalls/")
    assert "hazard" in out[0] and out[0]["recall_date"] == "2026-09-01T00:00:00"


# --- daily_scan ------------------------------------------------------------------


def test_daily_scan_recomputes_urgency():
    today = date(2026, 10, 4)
    target = next(
        t for t in store.get_tasks()
        if t["appliance_id"] == "fridge-garage" and t["task"] == "Clean condenser coils"
    )
    assert target["urgency"] == "ok"  # next_due is ~T+170 in the seed
    target["next_due"] = (today - timedelta(days=2)).isoformat()
    target["urgency"] = "ok"  # stale value, as if the scan never ran
    store.save_task(target)

    result = ha.daily_scan(today=today, cpsc_fn=lambda b, m: [])

    assert result["tasks_updated"] >= 1
    refreshed = next(
        t for t in store.get_tasks()
        if t["appliance_id"] == "fridge-garage" and t["task"] == "Clean condenser coils"
    )
    assert refreshed["urgency"] == "overdue"


def test_daily_scan_creates_recall_alerts_and_dedups(monkeypatch):
    monkeypatch.setattr(ha, "fetch_recalls", lambda timeout=90: FIXTURE_RECALLS)

    first = ha.daily_scan(today=date(2026, 10, 4))
    assert first["alerts_created"] == 1  # only Samsung RF28R7351SR matches

    alerts = [a for a in store.get_alerts() if a["alert_id"] == "recall-99991"]
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["kind"] == "recall"
    assert alert["appliance_id"] == "fridge-kitchen"
    assert alert["severity"] == "high"
    assert alert["read"] is False
    assert alert["source_url"].startswith("https://cpsc.gov/Recalls/")

    second = ha.daily_scan(today=date(2026, 10, 4))
    assert second["alerts_created"] == 0  # already recorded -> skipped


def test_daily_scan_refreshes_briefing(monkeypatch):
    monkeypatch.setattr(ha, "fetch_recalls", lambda timeout=90: FIXTURE_RECALLS)
    result = ha.daily_scan(today=date(2026, 10, 4))
    briefing = store.get_briefing()
    assert briefing is not None
    assert result["briefing_items"] == len(briefing["items"])
    assert any(i["type"] == "recall" for i in briefing["items"])


# --- onboard_from_photo -----------------------------------------------------------


def _fake_extract(image_bytes):
    assert image_bytes == b"fake-bytes"
    return {
        "brand": {"value": "TestBrand", "confidence": 0.9},
        "model": {"value": "TM-1000", "confidence": 0.6},
        "serial_number": {"value": "SN123", "confidence": 0.95},
        "manufacture_date": {"value": None, "confidence": 0.0},
    }


def test_onboard_from_photo():
    result = ha.onboard_from_photo(
        "uploads/tok123/photo.jpg", "tok123",
        category="refrigerator",
        extract_fn=_fake_extract,
        fetch_image=lambda key: b"fake-bytes",
    )
    assert result["brand"] == "TestBrand"
    assert result["model"] == "TM-1000"
    assert result["needs_confirmation"] is True  # min confidence 0.0 < 0.8
    assert result["tasks_created"] > 0

    appl = store.get_appliance(result["appliance_id"])
    assert appl["source"] == "photo"
    assert appl["serial_number"] == "SN123"
    assert appl["needs_confirmation"] is True

    tasks = store.get_tasks(result["appliance_id"])
    assert len(tasks) == result["tasks_created"] > 0


def test_onboard_requires_brand():
    with pytest.raises(ValueError, match="no brand"):
        ha.onboard_from_photo(
            "k", "t", category="refrigerator",
            extract_fn=lambda b: {"brand": {"value": None, "confidence": 0.0}},
            fetch_image=lambda k: b"x",
        )


def test_onboard_needs_image_fetcher():
    with pytest.raises(NotImplementedError, match="TODO\\(AWS creds\\)"):
        ha.onboard_from_photo("k", "t", category="refrigerator",
                              extract_fn=_fake_extract)


def test_bedrock_extract_not_wired():
    with pytest.raises(NotImplementedError, match="TODO\\(AWS creds\\)"):
        ha.bedrock_extract_nameplate(b"bytes")
    # ...but the prompt and schema are final and complete:
    assert "confidence" in ha.NAMEPLATE_PROMPT
    assert set(ha.NAMEPLATE_SCHEMA["required"]) == {
        "brand", "model", "serial_number", "manufacture_date"}


def test_classify_category_heuristic():
    catalog = store.get_catalog()
    assert ha._classify_category("Samsung refrigerator french door", catalog) == "refrigerator"
    assert ha._classify_category("Rheem water heater", catalog) == "water_heater"


def test_weekly_digest_stub():
    assert ha.weekly_digest()["status"] == "not_implemented"


# --- agent assembly -----------------------------------------------------------------


def test_build_agent_registers_tools(monkeypatch):
    if not ha._STRANDS_AVAILABLE:
        pytest.skip("strands-agents not installed")
    captured = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(ha, "Agent", FakeAgent)
    ha.build_agent()
    assert captured["name"] == "home_analyst"
    assert set(captured["tools"]) == {
        ha.dynamo_read, ha.dynamo_write, ha.catalog_lookup, ha.cpsc_search}
    assert "catalog" in captured["system_prompt"].lower()
