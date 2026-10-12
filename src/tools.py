"""The 7 HomeKeeper tools as plain functions: one implementation, two callers.

server.py registers these with FastMCP (the Alexa+ surface); the chat Lambda's
Bedrock tool loop calls the same functions, so both always return identical
shapes. Each returns a speakable `summary` plus structured fields for cards.
"""

import os

import store
from store import ApplianceLookupError, StoreUnavailable, get_catalog

UPLOAD_PAGE_URL = os.environ.get(
    "UPLOAD_PAGE_URL", "https://d250h79d4lokny.cloudfront.net/upload.html"
)

_DB_DOWN = {
    "status": "error",
    "summary": "HomeKeeper can't reach its records right now. Please try again in a moment.",
}


def _guard(fn, *args):
    try:
        return fn(*args)
    except StoreUnavailable:
        return dict(_DB_DOWN)
    except ApplianceLookupError as exc:
        return {"status": "needs_clarification", "summary": str(exc), "candidates": exc.candidates}


def _warranty_text(w: dict) -> str:
    if w["status"] == "active":
        return f"under warranty until {w['warranty_end']}"
    return "warranty expired" if w["status"] == "expired" else "warranty unknown"


def get_home_briefing(time_range_days: int = 30) -> dict:
    """Answer "what needs attention at home?".

    Call me when the user asks for an overview of everything due at home:
    upcoming maintenance, expiring warranties, or recalls -- e.g.
    "What needs attention at home?" or "Give me my home briefing."
    Returns items sorted by urgency (overdue first), each with a speakable
    summary plus structured fields for card rendering.
    """
    return _guard(store.get_briefing, time_range_days)


def _get_appliance(name: str) -> dict:
    matches = store.find_appliances(name)
    if len(matches) != 1:
        what = "matches several appliances" if matches else "matches nothing"
        return {
            "status": "needs_clarification",
            "summary": f"'{name}' {what}. Which one did you mean?",
            "candidates": [
                {"appliance_id": a.get("appliance_id"), "label": store._label(a)}
                for a in (matches or store.get_appliances())
            ],
        }
    appl = matches[0]
    tasks = sorted(
        store.get_tasks(appl["appliance_id"]),
        key=lambda t: t.get("last_done") or "",
        reverse=True,
    )
    warranty = store.warranty_status(appl)
    alerts = [
        {k: a.get(k) for k in ("kind", "title", "severity", "source_url")}
        for a in store.get_alerts(unread_only=True)
        if a.get("appliance_id") == appl["appliance_id"]
    ]
    label = store._label(appl)
    return {
        "summary": (
            f"{label}: {_warranty_text(warranty)}, {store.device_age(appl)} old. "
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
                "reorder_arranged_on": t.get("reorder_arranged_on"),
            }
            for t in tasks
        ],
        "alerts": alerts,
    }


def get_appliance(name: str) -> dict:
    """Look up one appliance's full record.

    Call me when the user asks about a specific device -- e.g.
    "When did I last flush the water heater?" or
    "Is my dishwasher still under warranty?"
    Name matching is fuzzy: "the fridge", "kitchen fridge", and "refrigerator"
    all work; if the phrase fits several appliances (or none), I return
    candidates so you can ask the user which one they mean.
    """
    return _guard(_get_appliance, name)


def _add_appliance(category, brand, model, room, install_date) -> dict:
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
        "summary": f"Added {label} with {len(res['tasks_created'])} scheduled maintenance tasks.",
        "appliance_id": appl["appliance_id"],
        "label": label,
        "warranty_end": appl.get("warranty_end"),
        "tasks": [{"task": t["task"], "next_due": t["next_due"]} for t in res["tasks_created"]],
    }


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
    return _guard(_add_appliance, category, brand, model, room, install_date)


def _start_photo_onboarding() -> dict:
    tok = store.create_upload_token()
    upload_url = f"{UPLOAD_PAGE_URL}?token={tok['token']}"
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


def start_photo_onboarding() -> dict:
    """Start photo-based onboarding.

    Call me when the user wants to add a device by photographing its nameplate
    -- e.g. "Add my new water heater" followed by choosing the photo option.
    Returns a one-time upload link (rendered as a QR card on Echo Show, or
    emailed on voice-only devices).
    """
    return _guard(_start_photo_onboarding)


def _log_maintenance(appliance, task, action, date) -> dict:
    try:
        res = store.log_maintenance(appliance, task, action, date)
    except ValueError as exc:
        return {"status": "error", "summary": str(exc)}
    when = "completed" if res["action"] == "done" else "snoozed for 30 days"
    return {
        "summary": f"Marked '{res['task']['task']}' as {when}. Next due {res['next_due']}.",
        "next_due": res["next_due"],
        "action": res["action"],
        "task": res["task"]["task"],
    }


def log_maintenance(appliance: str, task: str, action: str, date: str | None = None) -> dict:
    """Record a completed or snoozed maintenance task.

    Call me after the user finishes or postpones upkeep -- e.g.
    "I replaced the fridge filter" or "Remind me about the water heater next week."
    Action must be "done" or "snooze"; date defaults to today (YYYY-MM-DD).
    Returns the updated next-due date.
    """
    return _guard(_log_maintenance, appliance, task, action, date)


def _get_reorder_options(query: str) -> dict:
    res = store.get_reorder_options(query)
    if not res["options"]:
        return {
            "status": "not_found",
            "summary": (
                f"I couldn't find a consumable matching '{query}'. "
                "Try naming the appliance, like 'kitchen fridge filter' or 'smoke alarm battery'."
            ),
        }
    first = res["options"][0]
    out = {"options": res["options"], "marked_tasks": res["marked_tasks"]}
    base = f"Top pick: {first['consumable']} ({first['spec']})."
    if res["marked_tasks"]:
        out["summary"] = f"{base} I've noted that the replacement for '{res['marked_tasks'][0]}' is on order."
    elif len(res["appliances"]) > 1:
        out["status"] = "needs_clarification"
        out["summary"] = f"{base} That fits more than one appliance. Which one is it for?"
        out["candidates"] = [
            {"appliance_id": a["appliance_id"], "label": store._label(a)} for a in res["appliances"]
        ]
    else:
        out["summary"] = base
    return out


def get_reorder_options(appliance_or_consumable: str) -> dict:
    """Find consumable reorder options.

    Call me when the user wants to buy a replacement part -- e.g.
    "Reorder the kitchen fridge filter" or "I need a new smoke alarm battery."
    Returns the catalog spec plus an Amazon purchase link. When the phrase
    names one specific appliance, its matching maintenance reminder is marked
    "replacement ordered"; if it fits several, I return candidates to ask about.
    """
    return _guard(_get_reorder_options, appliance_or_consumable)


def _report_issue(appliance: str, symptom: str) -> dict:
    rep = store.get_issue_report(appliance, symptom)
    w, tri = rep["warranty"], rep["triage"]
    tri_txt = (
        f"Catalog guidance for '{tri['symptom']}': {tri['note']}"
        if tri
        else "No direct catalog match for this symptom."
    )
    return {
        "summary": f"{rep['label']}: {rep['device_age']} old, {_warranty_text(w)}. {tri_txt}",
        "appliance": {
            k: rep["appliance"].get(k)
            for k in ("appliance_id", "category", "brand", "model", "room", "install_date")
        },
        "label": rep["label"],
        "device_age": rep["device_age"],
        "warranty": w,
        "last_maintenance": rep["last_maintenance"],
        "open_tasks": rep["open_tasks"],
        "alerts": rep["alerts"],
        "triage": tri,
        "symptom": rep["symptom"],
    }


def report_issue(appliance: str, symptom: str) -> dict:
    """Triage an appliance problem.

    Call me when something is wrong -- e.g.
    "The dishwasher is making a strange noise" or "My fridge isn't cooling."
    I return warranty status, device age, last maintenance, related recalls,
    and catalog triage guidance. You (Alexa+) then reason over these facts to
    recommend warranty service, a self-check, or a professional -- the
    judgment call is yours, I supply the ground truth.
    """
    return _guard(_report_issue, appliance, symptom)


ALL = [
    get_home_briefing,
    get_appliance,
    add_appliance,
    start_photo_onboarding,
    log_maintenance,
    get_reorder_options,
    report_issue,
]
BY_NAME = {f.__name__: f for f in ALL}
