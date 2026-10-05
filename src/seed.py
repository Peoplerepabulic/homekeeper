"""Seed a realistic demo household into DynamoDB.

Dates are computed relative to DEMO_CLOCK (via db.get_today()), so the demo
day ALWAYS shows: 2 items due soon, 1 overdue, 1 warranty expiring soon,
1 recall -- regardless of when you run it.

Usage:
    python src/seed.py --dry-run   # print items as JSON without writing
    python src/seed.py             # write to DynamoDB (HOMEKEEPER_TABLE)
"""

import argparse
import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402
from scheduling import next_due_date, urgency  # noqa: E402


def slug(text: str) -> str:
    return text.lower().replace(" ", "-").replace("(", "").replace(")", "")


# (appliance_id, category, brand, model, room, install_offset_days, warranty_years)
APPLIANCES = [
    ("fridge-kitchen", "refrigerator", "Samsung", "RF28R7351SR", "kitchen", -900, 5),
    ("fridge-garage", "refrigerator", "Whirlpool", "WRT318FZDW", "garage", -400, 5),
    ("purifier-kitchen", "water_purifier", "iSpring", "RCC7", "kitchen", -500, 2),
    ("heater-garage", "water_heater", "Rheem", "XE50M12EC45U1", "garage", None, 6),  # warranty ends T+20
    ("hvac-main", "hvac", "Carrier", "24ACC636N003", "attic", -700, 10),
    ("hvac-upstairs", "hvac", "Carrier", "24ACC636N003", "attic", -700, 10),
    ("smoke-hallway", "smoke_alarm", "First Alert", "SA511CN2-3ST", "hallway", -1000, 10),
    ("smoke-bedroom", "smoke_alarm", "First Alert", "SA511CN2-3ST", "bedroom", -500, 10),
]

# (appliance_id, task, interval_days, last_done_offset_days) -> next_due is derived.
# Designed so the demo day shows: 1 overdue, 2 due soon, rest ok.
TASKS = [
    ("fridge-kitchen", "Clean condenser coils", 180, -60),
    ("fridge-kitchen", "Replace water filter", 180, -150),
    ("fridge-kitchen", "Check door seals", 365, -300),
    ("fridge-garage", "Clean condenser coils", 180, -10),
    ("fridge-garage", "Replace water filter", 180, -100),
    ("fridge-garage", "Check door seals", 365, -200),
    ("purifier-kitchen", "Replace filter cartridge", 180, -175),  # due T+5 (due soon)
    ("purifier-kitchen", "Sanitize system", 365, -200),
    ("purifier-kitchen", "Check TDS reading", 30, -5),
    ("heater-garage", "Flush tank sediment", 365, -300),
    ("heater-garage", "Inspect anode rod", 1095, -400),
    ("heater-garage", "Test T&P relief valve", 180, -20),
    ("hvac-main", "Replace air filter", 90, -102),  # due T-12 (OVERDUE)
    ("hvac-main", "Clean condensate drain", 180, -30),
    ("hvac-main", "Professional tune-up", 365, -330),
    ("hvac-upstairs", "Replace air filter", 90, -20),
    ("hvac-upstairs", "Clean condensate drain", 180, -100),
    ("hvac-upstairs", "Professional tune-up", 365, -100),
    ("smoke-hallway", "Monthly test", 30, -27),  # due T+3 (due soon)
    ("smoke-hallway", "Replace batteries", 365, -300),
    ("smoke-hallway", "Replace unit (10-year life)", 3650, -1000),
    ("smoke-bedroom", "Monthly test", 30, -10),
    ("smoke-bedroom", "Replace batteries", 365, -100),
    ("smoke-bedroom", "Replace unit (10-year life)", 3650, -500),
]


def build_items() -> list[dict]:
    today = db.get_today()
    items: list[dict] = []

    items.append({
        "sk": db.sk_profile(),
        "entity": "PROFILE",
        "household_name": "Demo Household",
        "timezone": "America/Chicago",
    })

    appl_lookup = {}
    for aid, category, brand, model, room, install_off, warranty_years in APPLIANCES:
        # Water heater: install date engineered so warranty ends exactly T+20.
        if aid == "heater-garage":
            install_date = today + timedelta(days=20 - warranty_years * 365)
        else:
            install_date = today + timedelta(days=install_off)
        warranty_end = install_date + timedelta(days=warranty_years * 365)
        appl_lookup[aid] = {"category": category, "brand": brand, "model": model, "room": room}
        items.append({
            "sk": db.sk_appliance(aid),
            "entity": "APPL",
            "appliance_id": aid,
            "category": category,
            "brand": brand,
            "model": model,
            "room": room,
            "install_date": install_date.isoformat(),
            "warranty_end": warranty_end.isoformat(),
            "confidence": "seed",
            "source": "seed",
        })

    briefing_items = []
    for aid, task, interval, last_done_off in TASKS:
        last_done = today + timedelta(days=last_done_off)
        due = next_due_date(last_done, interval)
        u = urgency(due, today)
        items.append({
            "sk": db.sk_task(aid, slug(task)),
            "entity": "TASK",
            "appliance_id": aid,
            "task_id": slug(task),
            "task": task,
            "interval_days": interval,
            "last_done": last_done.isoformat(),
            "next_due": due.isoformat(),
            "urgency": u,
            "snoozed": False,
        })
        if u in ("overdue", "due_soon"):
            a = appl_lookup[aid]
            briefing_items.append({
                "type": "maintenance",
                "appliance": f"{a['category'].replace('_', ' ').title()} ({a['room']})",
                "task": task,
                "due": due.isoformat(),
                "urgency": u,
            })

    # Warranty expiring soon: water heater warranty ends T+20.
    heater = appl_lookup["heater-garage"]
    briefing_items.append({
        "type": "warranty",
        "appliance": f"Water heater ({heater['room']})",
        "task": "Warranty expiring",
        "due": (today + timedelta(days=20)).isoformat(),
        "urgency": "due_soon",
        "detail": "Manufacturer warranty expires in 20 days.",
    })

    # One recall alert on the kitchen fridge (brand+model matched, like CPSC).
    items.append({
        "sk": db.sk_alert("recall-fridge-001"),
        "entity": "ALERT",
        "alert_id": "recall-fridge-001",
        "appliance_id": "fridge-kitchen",
        "kind": "recall",
        "severity": "high",
        "title": "Possible CPSC safety recall affecting this model",
        "source_url": "https://www.saferproducts.gov/",
        "read": False,
    })
    briefing_items.append({
        "type": "recall",
        "appliance": "Refrigerator (kitchen)",
        "task": "Safety recall",
        "due": None,
        "urgency": "high",
        "detail": "Possible CPSC recall affecting this model. See official recall link.",
    })

    # Sort: overdue first, then by due date. Mirrors the Phase 0 hardcoded briefing.
    order = {"overdue": 0, "high": 1, "due_soon": 2}
    briefing_items.sort(key=lambda i: (order.get(i["urgency"], 3), i["due"] or ""))
    items.append({
        "sk": db.sk_briefing(),
        "entity": "BRIEFING",
        "generated_at": today.isoformat(),
        "summary": (
            "You have 5 items needing attention: 1 overdue maintenance, "
            "2 due soon, 1 warranty expiring, and 1 safety recall."
        ),
        "items": briefing_items,
    })

    # One sample event log entry (cross-session memory evidence).
    items.append({
        "sk": db.sk_event(f"{today.isoformat()}T09:00:00"),
        "entity": "EVENT",
        "event_type": "seed",
        "detail": "Demo household seeded.",
    })

    return items


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the HomeKeeper demo household.")
    parser.add_argument("--dry-run", action="store_true", help="Print items as JSON without writing.")
    parser.add_argument("--user", default=db.DEMO_USER_ID, help="User id (default: DEMO_USER_ID).")
    args = parser.parse_args()

    items = build_items()
    if args.dry_run:
        print(json.dumps(items, indent=2, default=str))
        return

    for item in items:
        sk = item.pop("sk")
        db.put_item(sk, item, user_id=args.user)
    print(f"Seeded {len(items)} items for user '{args.user}' "
          f"(table={db.TABLE_NAME}, demo_clock={db.get_today().isoformat()}).")


if __name__ == "__main__":
    main()
