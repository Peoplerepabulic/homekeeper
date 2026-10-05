"""HomeKeeper MCP server (hot path).

Exposes 7 coarse-grained, intent-based tools to Alexa+. Every tool is a pure
database read/write plus static-catalog lookup -- no LLM calls on this path,
keeping p95 latency under 300 ms (Alexa+ requires < 500 ms round trips).

Business logic lives in store.py (DynamoDB with transparent local fallback);
this module is a thin MCP wrapper that shapes speakable responses.
"""

from fastmcp import FastMCP

import os
import store
from store import ApplianceLookupError, get_catalog

mcp = FastMCP("homekeeper")

# Real photo-upload page (CloudFront). The QR card encodes
# f"{_UPLOAD_PAGE_URL}?token=<token>"; the token row in DynamoDB is the auth.
_UPLOAD_PAGE_URL = os.environ.get(
    "UPLOAD_PAGE_URL", "https://d250h79d4lokny.cloudfront.net/upload.html"
)

# Last-resort briefing when neither DynamoDB nor the local seed can answer.
# Dates assume DEMO_CLOCK=2026-10-04.
_FALLBACK_BRIEFING = {
    "summary": (
        "You have 5 items needing attention: 1 overdue maintenance, "
        "2 due soon, 1 warranty expiring, and 1 safety recall."
    ),
    "items": [
        {
            "type": "maintenance",
            "appliance": "HVAC (attic)",
            "task": "Replace air filter",
            "due": "2026-09-22",
            "urgency": "overdue",
            "detail": "Filter was due 12 days ago. A clogged filter hurts efficiency.",
        },
        {
            "type": "maintenance",
            "appliance": "Smoke alarm (hallway)",
            "task": "Monthly test",
            "due": "2026-10-07",
            "urgency": "due_soon",
            "detail": "Test due in 3 days.",
        },
        {
            "type": "maintenance",
            "appliance": "Water purifier (kitchen)",
            "task": "Replace filter cartridge",
            "due": "2026-10-09",
            "urgency": "due_soon",
            "detail": "Filter cartridge due in 5 days.",
        },
        {
            "type": "warranty",
            "appliance": "Water heater (garage)",
            "task": "Warranty expiring",
            "due": "2026-10-24",
            "urgency": "due_soon",
            "detail": "Manufacturer warranty expires in 20 days.",
        },
        {
            "type": "recall",
            "appliance": "Refrigerator (kitchen)",
            "task": "Safety recall",
            "due": None,
            "urgency": "high",
            "detail": "Possible CPSC recall affecting this model. See official recall link.",
        },
    ],
}


def _candidates_payload(exc: ApplianceLookupError) -> dict:
    return {
        "status": "needs_clarification",
        "summary": str(exc),
        "candidates": exc.candidates,
    }


@mcp.tool()
def get_home_briefing(time_range_days: int = 30) -> dict:
    """Answer "what needs attention at home?".

    Call me when the user asks for an overview of everything due at home:
    upcoming maintenance, expiring warranties, or recalls -- e.g.
    "What needs attention at home?" or "Give me my home briefing."
    Returns items sorted by urgency (overdue first), each with a speakable
    summary plus structured fields for card rendering.
    """
    briefing = store.get_briefing(time_range_days)
    if briefing:
        return briefing
    return _FALLBACK_BRIEFING


@mcp.tool()
def get_appliance(name: str) -> dict:
    """Look up one appliance's full record.

    Call me when the user asks about a specific device -- e.g.
    "When did I last flush the water heater?" or
    "Is my dishwasher still under warranty?"
    Name matching is fuzzy: "the fridge", "kitchen fridge", and "refrigerator"
    all resolve to the same appliance. If nothing matches, I return candidates
    so you can ask the user which one they mean.
    """
    try:
        matches = store.find_appliances(name)
    except Exception as exc:  # noqa: BLE001 - never break the hot path
        return {"status": "error", "summary": f"Lookup failed: {exc}"}
    if len(matches) != 1:
        cands = [
            {"appliance_id": a.get("appliance_id"), "label": store._label(a)}
            for a in (matches or store.get_appliances())
        ]
        what = "matches several appliances" if matches else "matches nothing"
        return {
            "status": "needs_clarification",
            "summary": f"'{name}' {what}. Which one did you mean?",
            "candidates": cands,
        }
    appl = matches[0]
    tasks = sorted(
        store.get_tasks(appl["appliance_id"]),
        key=lambda t: t.get("last_done") or "",
        reverse=True,
    )
    warranty = store.warranty_status(appl)
    alerts = [
        {
            "kind": a.get("kind"),
            "title": a.get("title"),
            "severity": a.get("severity"),
            "source_url": a.get("source_url"),
        }
        for a in store.get_alerts()
        if a.get("appliance_id") == appl["appliance_id"] and not a.get("read")
    ]
    label = store._label(appl)
    warr_txt = (
        f"under warranty until {warranty['warranty_end']}"
        if warranty["status"] == "active"
        else ("warranty expired" if warranty["status"] == "expired" else "warranty unknown")
    )
    return {
        "summary": (
            f"{label}: {warr_txt}, {store.device_age(appl)} old. "
            f"{len(tasks)} maintenance tasks on file, {len(alerts)} open alerts."
        ),
        "appliance": {
            "appliance_id": appl.get("appliance_id"),
            "label": label,
            "category": appl.get("category"),
            "brand": appl.get("brand"),
            "model": appl.get("model"),
            "room": appl.get("room"),
            "install_date": appl.get("install_date"),
            "device_age": store.device_age(appl),
        },
        "warranty": warranty,
        "maintenance_history": [
            {
                "task": t["task"],
                "last_done": t.get("last_done"),
                "next_due": t.get("next_due"),
                "urgency": t.get("urgency"),
            }
            for t in tasks
        ],
        "alerts": alerts,
    }


@mcp.tool()
def add_appliance(
    category: str,
    brand: str | None = None,
    model: str | None = None,
    room: str | None = None,
    install_date: str | None = None,
) -> dict:
    """Create an appliance record by voice.

    Call me when the user adds a device conversationally -- e.g.
    "Add my new water heater" or "Remember the Samsung fridge in the kitchen."
    All fields except category are optional; a maintenance plan is generated
    from the static catalog based on the category.
    """
    try:
        res = store.add_appliance(category, brand, model, room, install_date)
    except ValueError as exc:
        return {
            "status": "error",
            "summary": str(exc),
            "valid_categories": sorted(c["display_name"] for c in get_catalog().values()),
        }
    appl = res["appliance"]
    label = store._label(appl)
    return {
        "summary": (
            f"Added {label} with {len(res['tasks_created'])} scheduled maintenance tasks."
        ),
        "appliance_id": appl["appliance_id"],
        "label": label,
        "warranty_end": appl.get("warranty_end"),
        "tasks": [
            {"task": t["task"], "next_due": t["next_due"]} for t in res["tasks_created"]
        ],
    }


@mcp.tool()
def start_photo_onboarding() -> dict:
    """Start photo-based onboarding.

    Call me when the user wants to add a device by photographing its nameplate
    -- e.g. "Add my new water heater" followed by choosing the photo option.
    Returns a one-time upload link (rendered as a QR card on Echo Show, or
    emailed on voice-only devices).
    """
    tok = store.create_upload_token()
    upload_url = f"{_UPLOAD_PAGE_URL}?token={tok['token']}"
    return {
        "summary": (
            "Photo onboarding started. Scan the QR code with your phone, "
            "photograph the appliance nameplate, and I'll build the record automatically."
        ),
        "upload_url": upload_url,
        "qr_text": upload_url,
        "expires_in_seconds": tok["expires_in_seconds"],
        "card_title": "Add an appliance",
        "card_body": "Scan to photograph the nameplate. The link expires in 15 minutes.",
    }


@mcp.tool()
def log_maintenance(
    appliance: str,
    task: str,
    action: str,
    date: str | None = None,
) -> dict:
    """Record a completed or snoozed maintenance task.

    Call me after the user finishes or postpones upkeep -- e.g.
    "I replaced the fridge filter" or "Remind me about the water heater next week."
    Action must be "done" or "snooze"; date defaults to today (YYYY-MM-DD).
    Returns the updated next-due date.
    """
    try:
        res = store.log_maintenance(appliance, task, action, date)
    except ApplianceLookupError as exc:
        return _candidates_payload(exc)
    except ValueError as exc:
        return {"status": "error", "summary": str(exc)}
    when = "completed" if res["action"] == "done" else "snoozed for 30 days"
    return {
        "summary": (
            f"Marked '{res['task']['task']}' as {when}. Next due {res['next_due']}."
        ),
        "next_due": res["next_due"],
        "action": res["action"],
        "task": res["task"]["task"],
    }


@mcp.tool()
def get_reorder_options(appliance_or_consumable: str) -> dict:
    """Find consumable reorder options.

    Call me when the user wants to buy a replacement part -- e.g.
    "Reorder the fridge filter" or "I need a new smoke alarm battery."
    Returns the spec from the catalog plus an Amazon purchase link, and marks
    the corresponding reminder as handled so the user is not nagged again.
    """
    res = store.get_reorder_options(appliance_or_consumable)
    if not res["options"]:
        return {
            "status": "not_found",
            "summary": (
                f"I couldn't find a consumable matching '{appliance_or_consumable}'. "
                "Try naming the appliance, like 'fridge filter' or 'smoke alarm battery'."
            ),
        }
    first = res["options"][0]
    return {
        "summary": (
            f"Found {len(res['options'])} option(s). Top pick: {first['consumable']} "
            f"({first['spec']}). I've marked the related reminder as handled."
        ),
        "options": res["options"],
    }


@mcp.tool()
def report_issue(appliance: str, symptom: str) -> dict:
    """Triage an appliance problem.

    Call me when something is wrong -- e.g.
    "The dishwasher is making a strange noise" or "My fridge isn't cooling."
    I return warranty status, device age, last maintenance, related recalls,
    and catalog triage guidance. You (Alexa+) then reason over these facts to
    recommend warranty service, a self-check, or a professional -- the
    judgment call is yours, I supply the ground truth.
    """
    try:
        rep = store.get_issue_report(appliance, symptom)
    except ApplianceLookupError as exc:
        return _candidates_payload(exc)
    w = rep["warranty"]
    warr_txt = (
        f"under warranty until {w['warranty_end']}"
        if w["status"] == "active"
        else ("warranty expired" if w["status"] == "expired" else "warranty unknown")
    )
    tri = rep["triage"]
    tri_txt = (
        f"Catalog guidance for '{tri['symptom']}': {tri['note']}"
        if tri
        else "No direct catalog match for this symptom."
    )
    return {
        "summary": (
            f"{rep['label']}: {rep['device_age']} old, {warr_txt}. {tri_txt}"
        ),
        "appliance": rep["label"],
        "device_age": rep["device_age"],
        "warranty": w,
        "last_maintenance": rep["last_maintenance"],
        "open_tasks": rep["open_tasks"],
        "alerts": rep["alerts"],
        "triage": tri,
    }


if __name__ == "__main__":
    # Streamable HTTP is required by the Alexa+ MCP spec (2025-11-25).
    # stateless_http keeps every request independent (no session affinity),
    # which is what the Lambda deployment needs.
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8000,
        stateless_http=True,
    )
