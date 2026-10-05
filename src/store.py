"""Repository layer for HomeKeeper's hot path.

Design:
  * When HOMEKEEPER_TABLE is set and DynamoDB answers, every read/write goes
    to the real single table (via db.py).
  * Otherwise -- env var unset, missing credentials, network failure -- the
    store transparently falls back to an in-memory copy of the seed household
    (seed.build_items()), so MCP Inspector and local tests run with zero
    configuration. The fallback is one-way: once DynamoDB fails, the process
    stays in local mode.
  * BRIEFING#latest is owned by the cold path in DynamoDB mode (read as-is).
    In local mode the briefing is recomputed from current state, so
    log_maintenance/add_appliance are immediately visible when testing locally.

All business logic lives here (fuzzy matching, plan generation, triage), so
it is unit-testable without fastmcp. server.py is a thin MCP wrapper.
"""

import os
import re
import time
import uuid
from datetime import date, timedelta
from urllib.parse import quote_plus

import db
from scheduling import next_due_date, urgency

CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog.json")

_catalog = None
_backend = None  # "dynamo" | "local", resolved lazily
_memory = None  # sk -> item, in-memory seed copy for local mode


class ApplianceLookupError(Exception):
    """Raised when a name resolves to zero or many appliances; carries candidates."""

    def __init__(self, message: str, candidates: list):
        super().__init__(message)
        self.candidates = candidates


# --- catalog ---------------------------------------------------------------


def get_catalog() -> dict:
    """Load the static appliance catalog (cached; no I/O after first load)."""
    global _catalog
    if _catalog is None:
        import json

        with open(CATALOG_PATH, encoding="utf-8") as f:
            _catalog = json.load(f)["categories"]
    return _catalog


# --- backend resolution -----------------------------------------------------


def _resolve_backend() -> str:
    global _backend
    if _backend is None:
        _backend = "dynamo" if os.environ.get("HOMEKEEPER_TABLE") else "local"
    return _backend


def is_local() -> bool:
    """True when running on the in-memory seed copy (no DynamoDB)."""
    return _resolve_backend() == "local"


def reset_local() -> None:
    """Force local mode with a fresh seed copy. Used by tests."""
    global _backend, _memory
    _backend = "local"
    _memory = None


def _mem() -> dict:
    global _memory
    if _memory is None:
        import seed

        _memory = {item["sk"]: dict(item) for item in seed.build_items()}
    return _memory


def _run(dynamo_fn, local_fn):
    """Run dynamo_fn(); on any AWS failure, fall back to local_fn() for good."""
    global _backend
    if _resolve_backend() == "dynamo":
        try:
            return dynamo_fn()
        except Exception as exc:  # noqa: BLE001 - any AWS failure -> local mode
            print(f"[store] DynamoDB unavailable, using local seed data: {exc}")
            _backend = "local"
    return local_fn()


def _all(prefix: str) -> list:
    return _run(
        lambda: db.query_prefix(prefix),
        lambda: [v for k, v in _mem().items() if k.startswith(prefix)],
    )


def _get(sk: str):
    return _run(
        lambda: db.get_item(sk),
        lambda: _mem().get(sk),
    )


def _put(sk: str, attrs: dict) -> dict:
    item = dict(attrs)
    item.pop("sk", None)

    def _dynamo():
        return db.put_item(sk, item)

    def _local():
        full = {"sk": sk, **item}
        _mem()[sk] = full
        return full

    return _run(_dynamo, _local)


# --- appliances -------------------------------------------------------------


def get_appliances() -> list:
    return _all("APPL#")


def get_appliance(appliance_id: str):
    return _get(db.sk_appliance(appliance_id))


def save_appliance(item: dict) -> dict:
    return _put(db.sk_appliance(item["appliance_id"]), item)


def get_tasks(appliance_id: str | None = None) -> list:
    tasks = _all("TASK#")
    if appliance_id is not None:
        tasks = [t for t in tasks if t.get("appliance_id") == appliance_id]
    return tasks


def save_task(item: dict) -> dict:
    return _put(db.sk_task(item["appliance_id"], item["task_id"]), item)


def get_alerts(unread_only: bool = False) -> list:
    alerts = _all("ALERT#")
    if unread_only:
        alerts = [a for a in alerts if not a.get("read")]
    return alerts


def save_alert(item: dict) -> dict:
    return _put(db.sk_alert(item["alert_id"]), item)


def mark_alert_handled(alert_id: str) -> None:
    alert = _get(db.sk_alert(alert_id))
    if alert:
        alert["reorder_arranged"] = True
        _put(db.sk_alert(alert_id), alert)


def log_event(event_type: str, detail: str) -> dict:
    ts = f"{db.get_today().isoformat()}T{time.strftime('%H:%M:%S')}"
    return _put(db.sk_event(ts), {"entity": "EVENT", "event_type": event_type, "detail": detail})


def create_upload_token(ttl_seconds: int = 900) -> dict:
    """Create a one-time photo-upload token (DynamoDB TTL = epoch seconds)."""
    token = uuid.uuid4().hex
    expires_at = int(time.time()) + ttl_seconds
    _put(db.sk_upload(token), {"entity": "UPLOAD", "token": token, "ttl": expires_at})
    return {"token": token, "expires_in_seconds": ttl_seconds}


# --- fuzzy name matching -----------------------------------------------------


ALIASES = {
    "fridge": "refrigerator",
    "freezer": "refrigerator",
    "ac": "central_ac",
    "a/c": "central_ac",
    "air conditioner": "central_ac",
    "heater": "water_heater",
    "furnace": "furnace",
    "washer": "clothes_washer",
    "washing machine": "clothes_washer",
    "dryer": "clothes_dryer",
    "oven": "oven_range",
    "stove": "oven_range",
    "range": "oven_range",
    "disposal": "garbage_disposal",
    "garage door": "garage_door_opener",
    "smoke detector": "smoke_alarm",
    "co alarm": "carbon_monoxide_alarm",
    "carbon monoxide detector": "carbon_monoxide_alarm",
    "thermostat": "hvac",
    "purifier": "water_purifier",
    "softener": "water_softener",
    "sump pump": "sump_pump",
    "doorbell": "doorbell",
    "sprinkler": "lawn_sprinkler",
    "fireplace": "chimney_fireplace",
    "chimney": "chimney_fireplace",
    "hood": "range_hood",
    "exhaust fan": "bathroom_exhaust_fan",
}


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"^(the|my|a|an)\s+", "", text)
    return re.sub(r"\s+", " ", text)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _haystack(appl: dict) -> str:
    cat = appl.get("category", "")
    display = get_catalog().get(cat, {}).get("display_name", "")
    return " ".join([
        appl.get("appliance_id", ""),
        cat.replace("_", " "),
        display.lower(),
        appl.get("brand", ""),
        appl.get("model", ""),
        appl.get("room", ""),
    ]).lower()


def find_appliances(name: str) -> list:
    """Fuzzy-match a user phrase to appliances. Returns [] | [one] | [many]."""
    query = _normalize(name)
    query = ALIASES.get(query, query)
    appliances = get_appliances()

    # 1. exact appliance id
    for a in appliances:
        if a.get("appliance_id", "").lower() == query:
            return [a]
    # 2. exact category (after alias expansion)
    exact = [a for a in appliances if a.get("category") == query]
    if exact:
        return exact
    # 3. substring both ways
    sub = [a for a in appliances if query in _haystack(a) or _haystack(a) in query]
    if sub:
        return sub
    # 4. token overlap (>= half the query tokens)
    tokens = set(query.split())
    scored = []
    for a in appliances:
        hay_tokens = set(_haystack(a).split())
        overlap = len(tokens & hay_tokens)
        if tokens and overlap >= max(1, len(tokens) / 2):
            scored.append((overlap, a))
    scored.sort(key=lambda x: -x[0])
    return [a for _, a in scored]


def _resolve_one(name: str) -> dict:
    matches = find_appliances(name)
    if len(matches) == 1:
        return matches[0]
    cands = [
        {
            "appliance_id": a.get("appliance_id"),
            "label": _label(a),
        }
        for a in (matches or get_appliances())
    ]
    if not matches:
        raise ApplianceLookupError(
            f"I couldn't find an appliance matching '{name}'. Which one did you mean?",
            cands,
        )
    raise ApplianceLookupError(
        f"'{name}' matches {len(matches)} appliances. Which one did you mean?",
        cands,
    )


def _label(appl: dict) -> str:
    cat = appl.get("category", "")
    display = get_catalog().get(cat, {}).get("display_name", cat.replace("_", " ").title())
    room = appl.get("room")
    return f"{display} ({room})" if room else display


# --- warranty / age -----------------------------------------------------------


def warranty_status(appl: dict, today: date | None = None) -> dict:
    today = today or db.get_today()
    end = appl.get("warranty_end")
    if not end:
        return {"status": "unknown", "warranty_end": None}
    end_d = date.fromisoformat(end)
    days_left = (end_d - today).days
    return {
        "status": "active" if days_left >= 0 else "expired",
        "warranty_end": end,
        "days_left": days_left,
    }


def device_age(appl: dict, today: date | None = None) -> str:
    today = today or db.get_today()
    start = appl.get("install_date")
    if not start:
        return "unknown"
    days = (today - date.fromisoformat(start)).days
    years, rest = divmod(max(days, 0), 365)
    if years:
        return f"{years} year{'s' if years > 1 else ''}"
    months, _ = divmod(rest, 30)
    return f"{months} month{'s' if months != 1 else ''}" if months else f"{rest} days"


# --- briefing ------------------------------------------------------------------


def _compute_briefing(time_range_days: int = 30) -> dict:
    """Build the briefing from current state (local mode; mirrors seed logic)."""
    today = db.get_today()
    appliances = {a["appliance_id"]: a for a in get_appliances()}
    items: list = []

    for t in get_tasks():
        due = date.fromisoformat(t["next_due"])
        u = urgency(due, today)
        # Demo-deterministic: only overdue/due_soon maintenance surface here,
        # mirroring seed.py (time_range_days governs the warranty window).
        if u in ("overdue", "due_soon"):
            days_out = (due - today).days
            a = appliances.get(t["appliance_id"], {})
            when = (
                f"{-days_out} days overdue"
                if u == "overdue"
                else ("due today" if days_out == 0 else f"due in {days_out} days")
            )
            items.append({
                "type": "maintenance",
                "appliance": _label(a),
                "task": t["task"],
                "due": t["next_due"],
                "urgency": u,
                "detail": f"{t['task']} — {when}.",
            })

    for a in appliances.values():
        w = warranty_status(a, today)
        if w["status"] == "active" and w["days_left"] <= time_range_days:
            items.append({
                "type": "warranty",
                "appliance": _label(a),
                "task": "Warranty expiring",
                "due": w["warranty_end"],
                "urgency": "due_soon",
                "detail": f"Manufacturer warranty expires in {w['days_left']} days.",
            })

    for al in get_alerts(unread_only=True):
        if al.get("kind") == "recall":
            a = appliances.get(al.get("appliance_id"), {})
            items.append({
                "type": "recall",
                "appliance": _label(a),
                "task": "Safety recall",
                "due": None,
                "urgency": "high",
                "detail": f"{al.get('title', 'Safety recall')}. See official recall link.",
                "source_url": al.get("source_url"),
            })

    order = {"overdue": 0, "high": 1, "due_soon": 2, "ok": 3}
    items.sort(key=lambda i: (order.get(i["urgency"], 3), i["due"] or ""))

    n_over = sum(1 for i in items if i["urgency"] == "overdue")
    n_soon = sum(1 for i in items if i["urgency"] == "due_soon" and i["type"] == "maintenance")
    n_war = sum(1 for i in items if i["type"] == "warranty")
    n_rec = sum(1 for i in items if i["type"] == "recall")
    parts = []
    if n_over:
        parts.append(f"{n_over} overdue maintenance")
    if n_soon:
        parts.append(f"{n_soon} due soon")
    if n_war:
        parts.append(f"{n_war} warranty expiring")
    if n_rec:
        parts.append(f"{n_rec} safety recall")
    summary = (
        f"You have {len(items)} items needing attention: {', '.join(parts)}."
        if parts
        else "Nothing needs attention right now. All clear!"
    )
    return {"summary": summary, "items": items}


def get_briefing(time_range_days: int = 30) -> dict | None:
    """BRIEFING#latest from DynamoDB; recomputed live in local mode."""
    if is_local():
        return _compute_briefing(time_range_days)
    item = _get(db.sk_briefing())
    if not item:
        return None
    return {"summary": item.get("summary", ""), "items": item.get("items", [])}


def compute_briefing(time_range_days: int = 30) -> dict:
    """Public: build the briefing from current state (used by the cold path)."""
    return _compute_briefing(time_range_days)


def save_briefing(briefing: dict) -> dict:
    """Write BRIEFING#latest. Owned by the cold path (daily scan / onboarding)."""
    item = {"entity": "BRIEFING", **briefing}
    return _put(db.sk_briefing(), item)


# --- mutations ------------------------------------------------------------------


def add_appliance(
    category: str,
    brand: str | None = None,
    model: str | None = None,
    room: str | None = None,
    install_date: str | None = None,
) -> dict:
    catalog = get_catalog()
    cat_key = _normalize(category or "").replace(" ", "_")
    cat_key = ALIASES.get(cat_key, cat_key)
    if cat_key not in catalog:
        valid = sorted(c["display_name"] for c in catalog.values())
        raise ValueError(
            f"Unknown appliance category '{category}'. Valid categories: {', '.join(valid)}."
        )
    today = db.get_today()
    start = None
    if install_date:
        try:
            start = date.fromisoformat(install_date)
        except ValueError:
            raise ValueError(f"install_date must be YYYY-MM-DD, got '{install_date}'.")
    if start and start > today:
        raise ValueError("install_date cannot be in the future.")

    base = _slug("-".join(p for p in [brand, model] if p) or f"{cat_key}-{room or 'home'}")
    appliance_id, n = base, 2
    while get_appliance(appliance_id):
        appliance_id, n = f"{base}-{n}", n + 1

    spec = catalog[cat_key]
    anchor = start or today
    warranty_end = (
        (start + timedelta(days=spec["typical_warranty_years"] * 365)).isoformat()
        if start
        else None
    )
    appliance = {
        "entity": "APPL",
        "appliance_id": appliance_id,
        "category": cat_key,
        "brand": brand,
        "model": model,
        "room": room,
        "install_date": start.isoformat() if start else None,
        "warranty_end": warranty_end,
        "confidence": "voice",
        "source": "voice",
    }
    save_appliance(appliance)

    created = []
    for t in spec["maintenance_tasks"]:
        due = next_due_date(anchor, t["interval_days"])
        task = {
            "entity": "TASK",
            "appliance_id": appliance_id,
            "task_id": _slug(t["task"]),
            "task": t["task"],
            "interval_days": t["interval_days"],
            "last_done": None,
            "next_due": due.isoformat(),
            "urgency": urgency(due, today),
            "snoozed": False,
        }
        save_task(task)
        created.append(task)

    log_event("appliance_added", f"Added {_label(appliance)} ({len(created)} tasks scheduled).")
    return {"appliance": appliance, "tasks_created": created}


def _match_task(tasks: list, query: str) -> dict:
    q = _normalize(query)
    for t in tasks:
        if _normalize(t["task"]) == q:
            return t
    subs = [t for t in tasks if q in _normalize(t["task"])]
    if len(subs) == 1:
        return subs[0]
    if not subs:
        names = ", ".join(t["task"] for t in tasks)
        raise ValueError(f"No task matching '{query}'. Tasks: {names}.")
    names = ", ".join(t["task"] for t in subs)
    raise ValueError(f"'{query}' matches several tasks ({names}). Please be more specific.")


def log_maintenance(
    appliance: str,
    task: str,
    action: str,
    date_str: str | None = None,
) -> dict:
    if action not in ("done", "snooze"):
        raise ValueError("Action must be 'done' or 'snooze'.")
    appl = _resolve_one(appliance)
    tasks = get_tasks(appl["appliance_id"])
    item = _match_task(tasks, task)
    today = db.get_today()

    if action == "done":
        done_on = today
        if date_str:
            try:
                done_on = date.fromisoformat(date_str)
            except ValueError:
                raise ValueError(f"date must be YYYY-MM-DD, got '{date_str}'.")
        item["last_done"] = done_on.isoformat()
        item["next_due"] = next_due_date(done_on, item["interval_days"]).isoformat()
        item["snoozed"] = False
    else:  # snooze: push the current due date out 30 days
        item["next_due"] = (
            date.fromisoformat(item["next_due"]) + timedelta(days=30)
        ).isoformat()
        item["snoozed"] = True
    item["urgency"] = urgency(date.fromisoformat(item["next_due"]), today)
    save_task(item)
    log_event(
        "maintenance_logged",
        f"{action} '{item['task']}' on {_label(appl)}; next due {item['next_due']}.",
    )
    return {"task": item, "next_due": item["next_due"], "action": action}


def get_reorder_options(query: str) -> dict:
    """Find consumables by appliance or consumable name; return Amazon links."""
    q = _normalize(query)
    catalog = get_catalog()
    options: list = []
    seen = set()
    matched_appliances: list = []

    def _add_option(appliance_id: str | None, name: str, keywords: str):
        key = (name, keywords)
        if key in seen:
            return
        seen.add(key)
        options.append({
            "consumable": name,
            "spec": keywords,
            "amazon_url": f"https://www.amazon.com/s?k={quote_plus(keywords)}",
            "appliance_id": appliance_id,
        })

    # appliance-scoped: fuzzy-match appliances, then list their consumables
    for appl in find_appliances(q):
        matched_appliances.append(appl)
        for c in catalog[appl["category"]]["consumables"]:
            _add_option(appl["appliance_id"], c["name"], c["search_keywords"])

    # catalog-wide: match consumable names/keywords directly.
    # Query tokens are alias-expanded ("fridge" -> "refrigerator") so
    # appliance-relevant consumables rank above generic ones.
    qtokens = set(q.split())
    expanded = {ALIASES.get(tok, tok) for tok in qtokens} | qtokens
    scored = []
    for cat_key, spec in catalog.items():
        for c in spec["consumables"]:
            hay = f"{c['name']} {c['search_keywords']}".lower()
            if q in hay:
                score = len(qtokens)  # exact phrase hit ranks first
            else:
                overlap = len(expanded & set(hay.split()))
                if overlap < max(1, len(qtokens) / 2):
                    continue
                score = overlap
            scored.append((score, c["name"], c["search_keywords"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    for _, name, keywords in scored:
        _add_option(None, name, keywords)

    # mark related reminders handled so the user is not nagged again
    for appl in matched_appliances:
        for al in get_alerts():
            if al.get("appliance_id") == appl["appliance_id"] and not al.get("read"):
                mark_alert_handled(al["alert_id"])

    return {"appliances": matched_appliances, "options": options}


def get_issue_report(appliance: str, symptom: str) -> dict:
    """Aggregate every fact Alexa+ needs to triage an issue (no judgment here)."""
    appl = _resolve_one(appliance)
    today = db.get_today()
    tasks = sorted(
        get_tasks(appl["appliance_id"]),
        key=lambda t: t.get("last_done") or "",
        reverse=True,
    )
    last_done = next((t for t in tasks if t.get("last_done")), None)
    alerts = [
        a for a in get_alerts()
        if a.get("appliance_id") == appl["appliance_id"] and not a.get("read")
    ]
    triage = _match_triage(appl["category"], symptom)
    return {
        "appliance": appl,
        "label": _label(appl),
        "device_age": device_age(appl, today),
        "warranty": warranty_status(appl, today),
        "last_maintenance": (
            {"task": last_done["task"], "date": last_done["last_done"]} if last_done else None
        ),
        "open_tasks": [
            {"task": t["task"], "next_due": t["next_due"], "urgency": t["urgency"]}
            for t in tasks
            if t.get("urgency") in ("overdue", "due_soon")
        ],
        "alerts": [
            {
                "kind": a.get("kind"),
                "title": a.get("title"),
                "severity": a.get("severity"),
                "source_url": a.get("source_url"),
            }
            for a in alerts
        ],
        "triage": triage,
        "symptom": symptom,
    }


def _match_triage(category: str, symptom: str) -> dict | None:
    triage = get_catalog().get(category, {}).get("fault_triage", {})
    s = _normalize(symptom)
    for key, val in triage.items():
        if _normalize(key) in s or s in _normalize(key):
            return {"symptom": key, **val}
    stokens = set(s.split()) - {"the", "a", "an", "my", "is", "it", "its"}
    best, best_score = None, 0
    for key, val in triage.items():
        score = len(stokens & set(_normalize(key).split()))
        if score > best_score:
            best, best_score = ({"symptom": key, **val}), score
    return best
