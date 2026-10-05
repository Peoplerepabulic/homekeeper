"""Home Analyst — the cold-path Strands agent for HomeKeeper.

Runs on AgentCore Runtime (async, never on the Alexa+ request path). Handles:

  * Task 1 — photo onboarding: S3 nameplate photo -> Bedrock Claude vision
    extraction -> catalog classification -> APPL#/TASK# rows -> briefing refresh.
  * Task 2 — daily scan: recompute task urgency, match appliances against the
    CPSC recall snapshot, write ALERT# rows, refresh BRIEFING#latest.
  * Task 3 — weekly digest (stub; SES wiring is a Phase 3 stretch goal).

Design notes
------------
* The Strands ``Agent`` is built by :func:`build_agent` and is only a thin
  reasoning loop over four tools (DynamoDB read/write, catalog lookup, CPSC
  search). All durable logic lives in the plain task functions below so it can
  be unit-tested without the SDK or AWS.
* Task functions take an injectable ``store`` (defaults to the real
  ``src.store`` module) and injectable side-effect functions
  (``extract_fn``, ``fetch_image``, ``cpsc_fn``), so tests run fully offline.
* ``strands`` is imported defensively: the task functions and tests work
  without the SDK installed; only :func:`build_agent` requires it
  (see ``agent/requirements.txt``).
"""

import argparse
import json
import os
import re
import sys
import time

# --- import path: reuse src/ (db key schema, store, scheduling) ----------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

import db  # noqa: E402  (DynamoDB single-table key schema)
import store  # noqa: E402  (hot-path business logic; local fallback for tests)
from scheduling import urgency  # noqa: E402
from datetime import date  # noqa: E402

try:  # The SDK is required only to *build* the agent, not to test the tasks.
    from strands import Agent, tool

    _STRANDS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised when SDK is absent

    _STRANDS_AVAILABLE = False

    def tool(fn=None, **kwargs):
        """No-op @tool fallback so task functions stay importable/testable."""

        def _wrap(f):
            return f

        return _wrap(fn) if callable(fn) else _wrap

    Agent = None


# --- CPSC recall client ---------------------------------------------------------
# Verified 2026-10-04; full notes in docs/cpsc-api-notes.md.
# The public endpoint has no server-side search: every request returns the
# full ~28 MB dump, so we cache one snapshot per day and match locally.

CPSC_URL = "https://www.saferproducts.gov/RestWebServices/Recall"
_CPSC_CACHE = {"at": 0.0, "recalls": []}
CPSC_CACHE_TTL = 24 * 3600

# The endpoint's WAF rejects the default `python-requests/*` User-Agent
# (HTTP 403, verified 2026-10-04); any other UA works.
CPSC_USER_AGENT = "HomeKeeper/1.0 (AWS hackathon demo)"


def fetch_recalls(timeout: int = 90) -> list:
    """Download the full CPSC recall dump (24h in-process cache). Cold path only."""
    import requests

    now = time.time()
    if _CPSC_CACHE["recalls"] and now - _CPSC_CACHE["at"] < CPSC_CACHE_TTL:
        return _CPSC_CACHE["recalls"]
    resp = requests.get(
        CPSC_URL,
        params={"Format": "json"},
        headers={"User-Agent": CPSC_USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()
    _CPSC_CACHE["recalls"] = resp.json()
    _CPSC_CACHE["at"] = now
    return _CPSC_CACHE["recalls"]


def _default_cpsc_fn(brand: str, model: str) -> list:
    """Default CPSC lookup for daily_scan: the cpsc_search tool, parsed."""
    return json.loads(cpsc_search(brand, model))


def _norm(text: str | None) -> str:
    """Lowercase alphanumeric fold: 'YD-001' and 'YD 001' compare equal."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _recall_text(recall: dict) -> str:
    """All text surfaces a brand/model can appear in (normalized).

    The API has no dedicated brand field and Products[].Model is always
    empty (verified 2026-10-04), so brand and model are both matched as
    substrings of the recall's prose.
    """
    parts = [recall.get("Title") or "", recall.get("Description") or ""]
    for p in recall.get("Products") or []:
        parts.append(p.get("Name") or "")
        parts.append(p.get("Description") or "")
    return _norm(" ".join(parts))


def match_recalls(recalls: list, brand: str | None, model: str | None) -> list:
    """Return recalls where brand AND model both match (case-insensitive).

    A recall is flagged "possibly affected" only when both substrings are
    found — never on brand alone. Empty brand or model -> no match, to avoid
    false positives. Short model numbers can still over-match; callers must
    keep the "possibly affected" wording, never "recalled".
    """
    b, m = _norm(brand), _norm(model)
    if not b or not m:
        return []
    return [r for r in recalls if b in _recall_text(r) and m in _recall_text(r)]


# --- agent tools -----------------------------------------------------------------


@tool
def dynamo_read(sk_prefix: str) -> str:
    """Read HomeKeeper state from the DynamoDB single table by sort-key prefix.

    Call when you need current state. Useful prefixes: "APPL#" (appliances),
    "TASK#" (maintenance tasks), "ALERT#" (alerts), "BRIEFING#latest"
    (precomputed briefing), "PROFILE" (household). Example: dynamo_read("APPL#").
    Returns a JSON array of items.
    """
    return json.dumps(db.query_prefix(sk_prefix), default=str)


@tool
def dynamo_write(sk: str, attrs_json: str) -> str:
    """Write one item to the HomeKeeper single table.

    sk is the sort key, e.g. "ALERT#recall-12345". attrs_json is a JSON
    object of attributes (pk/sk are added automatically). Returns the
    written item as JSON.
    """
    attrs = json.loads(attrs_json)
    if not isinstance(attrs, dict):
        raise ValueError("attrs_json must be a JSON object")
    return json.dumps(db.put_item(sk, attrs), default=str)


@tool
def catalog_lookup(category: str) -> str:
    """Look up the static appliance catalog for one category.

    Returns the category's maintenance tasks and intervals, consumables with
    search keywords, typical warranty years, and fault-triage guidance.
    Use this — never your own knowledge — to decide how often something
    needs maintenance. Example: catalog_lookup("refrigerator").
    """
    catalog = store.get_catalog()
    spec = catalog.get(category)
    if spec is None:
        return json.dumps({"error": f"unknown category '{category}'",
                           "valid": sorted(catalog.keys())})
    return json.dumps(spec)


@tool
def cpsc_search(brand: str, model: str) -> str:
    """Search CPSC safety recalls for a brand + model.

    Returns a JSON array of possibly-affected recalls, each with recall_id,
    title, url (official cpsc.gov link), recall_date, and hazard. A recall
    is included only when BOTH brand and model match the recall text.
    Empty brand or model returns []. Example: cpsc_search("Samsung", "RF28R7351SR").
    """
    matches = match_recalls(fetch_recalls(), brand, model)
    slim = [
        {
            "recall_id": r.get("RecallID"),
            "title": r.get("Title"),
            "url": r.get("URL"),
            "recall_date": r.get("RecallDate"),
            "hazard": ", ".join(
                h.get("Name", "") for h in (r.get("Hazards") or []) if h.get("Name")
            ),
        }
        for r in matches
    ]
    return json.dumps(slim)


# --- Bedrock vision extraction (Task 1) ------------------------------------------

# Final prompt + schema. The live call needs AWS credentials (bedrock-runtime),
# so the function below raises NotImplementedError until Phase 2 deployment.

NAMEPLATE_PROMPT = """\
You are a precise data-extraction assistant. Read the appliance nameplate / \
rating label in the image and extract the fields below. Return ONLY valid JSON \
matching the schema — no markdown, no commentary.

Rules:
- "value": the exact text as printed, or null if not visible or not legible.
- "confidence": 0.0-1.0, your certainty the value is correct. Use below 0.8 \
for blurry, cropped, or ambiguous text.
- brand: the manufacturer / brand name (e.g. "Samsung", "Rheem"). Do not \
confuse it with the retailer or the model line.
- model: the model number (e.g. "RF28R7351SR"). Often labeled "Model", \
"Model No.", or "MOD".
- serial_number: often labeled "Serial", "Serial No.", or "S/N".
- manufacture_date: often labeled "Mfg. Date", "Date of Manufacture", or \
"DOM". Normalize to YYYY-MM-DD when possible; null if the date is coded in a \
way you cannot decode.
- Never invent values. A missing field is null with confidence 0.0, not a guess.\
"""

NAMEPLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "brand": {
            "type": "object",
            "properties": {
                "value": {"type": ["string", "null"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["value", "confidence"],
        },
        "model": {
            "type": "object",
            "properties": {
                "value": {"type": ["string", "null"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["value", "confidence"],
        },
        "serial_number": {
            "type": "object",
            "properties": {
                "value": {"type": ["string", "null"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["value", "confidence"],
        },
        "manufacture_date": {
            "type": "object",
            "properties": {
                "value": {"type": ["string", "null"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["value", "confidence"],
        },
    },
    "required": ["brand", "model", "serial_number", "manufacture_date"],
    "additionalProperties": False,
}


def bedrock_extract_nameplate(image_bytes: bytes) -> dict:
    """Extract nameplate fields via Bedrock Claude vision.

    TODO(AWS creds): wire boto3 bedrock-runtime `converse` with
    NAMEPLATE_PROMPT + NAMEPLATE_SCHEMA (response_format json), model id
    from BEDROCK_MODEL_ID. Until then this raises NotImplementedError —
    tests inject their own extract_fn.
    """
    raise NotImplementedError(
        "TODO(AWS creds): Bedrock Claude vision call not wired yet. "
        "Needs a boto3 bedrock-runtime client and model access in us-east-1. "
        f"Prompt ({len(NAMEPLATE_PROMPT)} chars) and NAMEPLATE_SCHEMA are final."
    )


def _classify_category(text: str, catalog: dict) -> str:
    """Heuristic catalog classification by token overlap.

    Production path: the agent itself picks the category via catalog_lookup
    + LLM judgment; this is the deterministic fallback used by
    onboard_from_photo when no category is supplied.
    """
    tokens = set(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())
    best, best_score = None, 0
    for key, spec in catalog.items():
        hay = f"{key} {spec.get('display_name', '')}".lower()
        hay_tokens = set(re.sub(r"[^a-z0-9 ]", " ", hay).split())
        score = len(tokens & hay_tokens)
        if score > best_score:
            best, best_score = key, score
    return best or "refrigerator"


# --- Task 1: photo onboarding ------------------------------------------------------


def onboard_from_photo(
    s3_key: str,
    upload_token: str,
    category: str | None = None,
    store_mod=store,
    extract_fn=bedrock_extract_nameplate,
    fetch_image=None,
) -> dict:
    """Onboard one appliance from a nameplate photo (cold path, async).

    Pipeline: fetch image bytes from S3 -> Bedrock Claude vision extraction
    (brand/model/serial/mfg date, each with confidence) -> catalog
    classification -> APPL# + TASK# rows (maintenance plan from the catalog,
    never from the model) -> BRIEFING#latest refresh.

    Quality control: fields with confidence < 0.8 set needs_confirmation;
    the hot path's get_appliance then asks "I read the model as X, correct?".
    """
    # 1. image bytes (S3; needs AWS creds in production)
    if fetch_image is None:
        raise NotImplementedError(
            f"TODO(AWS creds): fetch image bytes from S3 (s3_key={s3_key!r}). "
            "Pass fetch_image= in tests."
        )
    image_bytes = fetch_image(s3_key)

    # 2. vision extraction
    fields = extract_fn(image_bytes)
    brand = (fields.get("brand") or {}).get("value")
    model = (fields.get("model") or {}).get("value")
    if not brand:
        raise ValueError("Nameplate extraction found no brand; cannot onboard.")
    confidences = [
        (f or {}).get("confidence", 0.0)
        for f in fields.values() if isinstance(f, dict)
    ]
    confidence = round(min(confidences), 2) if confidences else 0.0

    # 3. catalog classification
    catalog = store_mod.get_catalog()
    category = category or _classify_category(f"{brand} {model}", catalog)

    # 4. APPL# + TASK# (maintenance plan comes from the catalog via
    #    store.add_appliance; the model only supplies brand/model)
    result = store_mod.add_appliance(category, brand=brand, model=model)
    appl = result["appliance"]
    appl.update({
        "confidence": confidence,
        "source": "photo",
        "serial_number": (fields.get("serial_number") or {}).get("value"),
        "manufacture_date": (fields.get("manufacture_date") or {}).get("value"),
        "photo_s3_key": s3_key,
        "upload_token": upload_token,
        "needs_confirmation": confidence < 0.8,
    })
    store_mod.save_appliance(appl)
    store_mod.log_event(
        "photo_onboarded",
        f"Photo onboarding created {appl['appliance_id']} "
        f"(confidence {confidence}, category {category}).",
    )

    # 5. refresh the precomputed briefing
    briefing = store_mod.compute_briefing()
    store_mod.save_briefing(briefing)

    return {
        "appliance_id": appl["appliance_id"],
        "category": category,
        "brand": brand,
        "model": model,
        "confidence": confidence,
        "needs_confirmation": appl["needs_confirmation"],
        "tasks_created": len(result["tasks_created"]),
    }


# --- Task 2: daily scan -------------------------------------------------------------


def daily_scan(today: "date | None" = None, store_mod=store, cpsc_fn=None) -> dict:
    """Nightly cold-path scan (EventBridge trigger).

    1. Recompute every task's urgency against `today`.
    2. For each appliance with brand+model, search CPSC recalls; a recall is
       recorded only when brand AND model both match ("possibly affected",
       with the official cpsc.gov link). Already-recorded recalls are skipped.
    3. Rewrite BRIEFING#latest so the hot path serves it with one GetItem.
    """
    today = today or db.get_today()
    if cpsc_fn is None:
        cpsc_fn = _default_cpsc_fn

    tasks_updated = 0
    for task in store_mod.get_tasks():
        new_urgency = urgency(date.fromisoformat(task["next_due"]), today)
        if task.get("urgency") != new_urgency:
            task["urgency"] = new_urgency
            store_mod.save_task(task)
            tasks_updated += 1

    seen = {
        (a.get("kind"), str(a.get("recall_id") or a.get("alert_id")))
        for a in store_mod.get_alerts()
        if a.get("kind") == "recall"
    }
    alerts_created = 0
    for appl in store_mod.get_appliances():
        brand, model = appl.get("brand"), appl.get("model")
        if not brand or not model:
            continue
        for r in cpsc_fn(brand, model):
            key = ("recall", str(r.get("recall_id")))
            if key in seen:
                continue
            seen.add(key)
            store_mod.save_alert({
                "entity": "ALERT",
                "alert_id": f"recall-{r.get('recall_id')}",
                "kind": "recall",
                "appliance_id": appl["appliance_id"],
                "title": r.get("title") or "Possible CPSC safety recall",
                "severity": "high",
                "source_url": r.get("url"),
                "recall_date": r.get("recall_date"),
                "recall_id": str(r.get("recall_id")),
                "hazard": r.get("hazard", ""),
                "read": False,
            })
            alerts_created += 1

    briefing = store_mod.compute_briefing()
    store_mod.save_briefing(briefing)
    store_mod.log_event(
        "daily_scan",
        f"tasks_updated={tasks_updated} alerts_created={alerts_created} "
        f"briefing_items={len(briefing['items'])}",
    )
    return {
        "date": today.isoformat(),
        "tasks_updated": tasks_updated,
        "alerts_created": alerts_created,
        "briefing_items": len(briefing["items"]),
    }


# --- Task 3: weekly digest (stub) ------------------------------------------------------


def weekly_digest(store_mod=store):
    """TODO(Phase 3): weekly email digest via SES.

    Stretch goal — kept as a stub so the agent's tool surface is complete.
    Not wired: needs SES sender identity + recipient opt-in.
    """
    return {"status": "not_implemented", "todo": "SES weekly digest (Phase 3 stretch)"}


# --- agent assembly ------------------------------------------------------------------


SYSTEM_PROMPT = """\
You are Home Analyst, the cold-path agent for HomeKeeper, a home-appliance
manager running on Alexa+. You never talk to users directly; you maintain the
DynamoDB single table that the hot-path MCP server reads from.

Rules:
- Maintenance rules come ONLY from catalog_lookup. The language model extracts
  brand/model from photos; "how often to service it" always comes from the
  catalog table.
- A CPSC recall is recorded only when BOTH brand and model match the recall
  text. Word alerts as "possibly affected" and always include the official
  cpsc.gov URL — never claim a specific unit IS recalled.
- After photo onboarding or a daily scan, always rewrite BRIEFING#latest via
  dynamo_write so the hot path serves fresh data with one read.
- All writes go through dynamo_write with the documented key layout:
  APPL#<id>, TASK#<applianceId>#<taskId>, ALERT#<alertId>, BRIEFING#latest,
  EVENT#<timestamp>. The partition key is added automatically.
"""


def build_agent():
    """Assemble the Strands agent.

    Call on AgentCore Runtime (or anywhere with AWS credentials), NOT in
    local dev: with no explicit model, Strands eagerly constructs a Bedrock
    boto3 client at Agent() creation time (verified 2026-10-04), so building
    the agent requires AWS credentials even before any inference runs.
    """
    if not _STRANDS_AVAILABLE:
        raise RuntimeError(
            "strands-agents is not installed; see agent/requirements.txt."
        )
    kwargs = {}
    model_id = os.environ.get("BEDROCK_MODEL_ID")
    if model_id:
        kwargs["model"] = model_id  # e.g. a Bedrock Claude model id
    return Agent(
        name="home_analyst",
        system_prompt=SYSTEM_PROMPT,
        tools=[dynamo_read, dynamo_write, catalog_lookup, cpsc_search],
        **kwargs,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Home Analyst cold-path tasks")
    parser.add_argument("--daily-scan", action="store_true",
                        help="run daily_scan against the local store")
    parser.add_argument("--onboard-photo", nargs=2, metavar=("S3_KEY", "TOKEN"),
                        help="run onboard_from_photo (needs fetch_image/extract_fn)")
    args = parser.parse_args()
    if args.daily_scan:
        print(json.dumps(daily_scan(), indent=2, default=str))
    elif args.onboard_photo:
        print(json.dumps(
            onboard_from_photo(args.onboard_photo[0], args.onboard_photo[1]),
            indent=2, default=str))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
