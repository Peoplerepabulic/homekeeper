"""HomeKeeper MCP server (hot path).

Exposes 7 coarse-grained, intent-based tools to Alexa+. Every tool is a pure
database read/write plus static-catalog lookup -- no LLM calls on this path,
keeping p95 latency under 300 ms (Alexa+ requires < 500 ms round trips).

Phase 0: get_home_briefing returns hardcoded demo data; the other six tools
are stubs marked TODO(Phase 1).
"""

from fastmcp import FastMCP

mcp = FastMCP("homekeeper", stateless_http=True)


@mcp.tool()
def get_home_briefing(time_range_days: int = 30) -> dict:
    """Answer "what needs attention at home?".

    Call me when the user asks for an overview of everything due at home:
    upcoming maintenance, expiring warranties, or recalls -- e.g.
    "What needs attention at home?" or "Give me my home briefing."
    Returns items sorted by urgency (overdue first), each with a speakable
    summary plus structured fields for card rendering.
    """
    # Phase 0: hardcoded demo briefing. Dates assume DEMO_CLOCK=2026-10-04.
    # TODO(Phase 1): read BRIEFING#latest from DynamoDB instead.
    return {
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
    # TODO(Phase 1): implement DynamoDB lookup + fuzzy name matching.
    return {
        "status": "not_implemented",
        "summary": f"Appliance lookup for '{name}' is not implemented yet (Phase 1).",
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
    # TODO(Phase 1): implement DynamoDB write + catalog-based plan generation.
    return {
        "status": "not_implemented",
        "summary": f"Voice onboarding for '{category}' is not implemented yet (Phase 1).",
    }


@mcp.tool()
def start_photo_onboarding() -> dict:
    """Start photo-based onboarding.

    Call me when the user wants to add a device by photographing its nameplate
    -- e.g. "Add my new water heater" followed by choosing the photo option.
    Returns a one-time upload link (rendered as a QR card on Echo Show, or
    emailed on voice-only devices).
    """
    # TODO(Phase 1): implement UPLOAD# token generation with TTL.
    return {
        "status": "not_implemented",
        "summary": "Photo onboarding is not implemented yet (Phase 1).",
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
    if action not in ("done", "snooze"):
        return {"status": "error", "summary": "Action must be 'done' or 'snooze'."}
    # TODO(Phase 1): implement DynamoDB write + next-due recomputation.
    return {
        "status": "not_implemented",
        "summary": f"Logging '{action}' for '{task}' on '{appliance}' is not implemented yet (Phase 1).",
    }


@mcp.tool()
def get_reorder_options(appliance_or_consumable: str) -> dict:
    """Find consumable reorder options.

    Call me when the user wants to buy a replacement part -- e.g.
    "Reorder the fridge filter" or "I need a new smoke alarm battery."
    Returns the spec from the catalog plus an Amazon purchase link, and marks
    the corresponding reminder as handled so the user is not nagged again.
    """
    # TODO(Phase 1): implement catalog lookup + Amazon search-link generation.
    return {
        "status": "not_implemented",
        "summary": f"Reorder options for '{appliance_or_consumable}' are not implemented yet (Phase 1).",
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
    # TODO(Phase 1): implement multi-read aggregation + catalog triage.
    return {
        "status": "not_implemented",
        "summary": f"Issue triage for '{appliance}' is not implemented yet (Phase 1).",
    }


if __name__ == "__main__":
    # Streamable HTTP is required by the Alexa+ MCP spec (2025-11-25).
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
