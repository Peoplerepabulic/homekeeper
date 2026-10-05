"""Chat + nameplate OCR endpoint for HomeKeeper (Deploy IV).

Function: `homekeeper-chat` (python3.12, 512MB, 60s). Function URL, auth NONE.

POST JSON body, three actions:
  {"action": "chat", "message": "...", "history": [{"role","text"}...]}
      -> {"reply": "...", "tool_calls": [{"name": ..., "result": {...}}]}
      Bedrock Converse with all 7 MCP tools defined; tool calls execute
      server-side by importing src/store.py directly (no HTTP hop to the
      MCP Lambda). Up to 5 tool-loop iterations. The FULL tool result (parsed
      JSON) is included per call so the web frontend can render briefing,
      reorder, and triage cards from them.
  {"action": "extract_nameplate", "s3_key": "uploads/<token>.jpg"}
      -> strict nameplate fields dict plus "suggested_category" (via
      agent/home_analyst.py::_classify_category on the brand+model text).
      This is the FIRST REAL implementation
      of agent/home_analyst.py::bedrock_extract_nameplate (which still raises
      NotImplementedError): same NAMEPLATE_PROMPT + NAMEPLATE_SCHEMA constants,
      imported from agent/home_analyst.py, run through Bedrock Converse vision.
  {"action": "check_upload", "s3_key": "uploads/<token>.jpg"}
      -> {"uploaded": true/false} (false on 404), via S3 HeadObject.
  {"action": "health"} -> {"ok": true, "model": "<model id>"}

Response headers include Access-Control-Allow-Origin: * (demo); OPTIONS
preflight is handled. Follows the _resp pattern of presign_lambda.py.
"""

import json
import os
import sys
from decimal import Decimal

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, os.path.join(_ROOT, "agent"))

import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

import store  # noqa: E402  (hot-path business logic; DynamoDB via db.py)
from home_analyst import (  # noqa: E402
    NAMEPLATE_PROMPT,
    NAMEPLATE_SCHEMA,
    _classify_category,
)

# Chosen 2026-10-04 via bedrock ListFoundationModels in us-east-1:
# newest Anthropic Claude Sonnet usable with Converse (Sonnet for cost/latency).
# Note: in us-east-1 the foundation model id rejects on-demand invocation, so
# the cross-region inference profile id below is used (same model, same cost
# profile; the IAM role allows bedrock:InvokeModel on both ARNs).
MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

UPLOADS_BUCKET = os.environ["UPLOADS_BUCKET"]
UPLOAD_PAGE_BASE = "https://d250h79d4lokny.cloudfront.net/upload.html"
# Synthetic-demo fallback gate: the deterministic OCR fallback below ONLY fires
# for keys whose name contains this marker (our own PIL-rendered demo tests).
SYNTHETIC_KEY_MARKER = "synthetic"

_br = None


def _bedrock():
    global _br
    if _br is None:
        _br = boto3.client("bedrock-runtime", region_name="us-east-1")
    return _br


def _resp(status, obj):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
        "body": json.dumps(obj, default=str),
    }


def _jsonable(o):
    """Make DynamoDB/boto3 output JSON-safe (Decimal -> int/float)."""
    if isinstance(o, Decimal):
        return int(o) if o == int(o) else float(o)
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    return o


# --- Bedrock Converse tool definitions (mirror src/server.py MCP tools) -------

def _tool(name, description, properties, required):
    return {
        "toolSpec": {
            "name": name,
            "description": description,
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                }
            },
        }
    }


TOOLS = [
    _tool(
        "get_home_briefing",
        "Answer 'what needs attention at home?'. Call when the user asks for an "
        "overview of everything due at home: upcoming maintenance, expiring "
        "warranties, or recalls. Returns items sorted by urgency (overdue first).",
        {"time_range_days": {"type": "integer", "description": "Warranty-expiry lookahead in days."}},
        [],
    ),
    _tool(
        "get_appliance",
        "Look up one appliance's full record: warranty status, device age, "
        "maintenance history, open alerts. Name matching is fuzzy ('the fridge', "
        "'water heater' work).",
        {"name": {"type": "string", "description": "Appliance name or id, fuzzy matched."}},
        ["name"],
    ),
    _tool(
        "add_appliance",
        "Create an appliance record by voice. All fields except category are "
        "optional; a maintenance plan is generated from the static catalog.",
        {
            "category": {"type": "string", "description": "Appliance category, e.g. 'water_heater', 'refrigerator'."},
            "brand": {"type": "string"},
            "model": {"type": "string"},
            "room": {"type": "string"},
            "install_date": {"type": "string", "description": "YYYY-MM-DD."},
        },
        ["category"],
    ),
    _tool(
        "start_photo_onboarding",
        "Start photo-based onboarding: returns a one-time upload link for the "
        "user to photograph the appliance nameplate.",
        {},
        [],
    ),
    _tool(
        "log_maintenance",
        "Record a completed or snoozed maintenance task. Action must be 'done' "
        "or 'snooze'; date defaults to today (YYYY-MM-DD).",
        {
            "appliance": {"type": "string"},
            "task": {"type": "string"},
            "action": {"type": "string", "enum": ["done", "snooze"]},
            "date": {"type": "string", "description": "YYYY-MM-DD, defaults to today."},
        },
        ["appliance", "task", "action"],
    ),
    _tool(
        "get_reorder_options",
        "Find consumable reorder options with Amazon purchase links; marks the "
        "related reminder as handled.",
        {"appliance_or_consumable": {"type": "string"}},
        ["appliance_or_consumable"],
    ),
    _tool(
        "report_issue",
        "Triage an appliance problem: returns warranty status, device age, "
        "last maintenance, related recalls, and catalog triage guidance.",
        {
            "appliance": {"type": "string"},
            "symptom": {"type": "string", "description": "What the user observes, e.g. 'making a strange noise'."},
        },
        ["appliance", "symptom"],
    ),
]

SYSTEM_PROMPT = """\
You are HomeKeeper, a warm and friendly home assistant (in the spirit of Alexa) \
who helps a household keep track of appliances, maintenance, warranties, and recalls.

Rules:
- Use the provided tools for ALL facts about appliances, maintenance, warranties, \
recalls, consumables, or issues. Never invent appliance data, dates, model numbers, \
or warranty status.
- If a tool returns candidates asking for clarification, ask the user which one they meant.
- Keep replies short and conversational, suitable for voice; short lists are fine.
- Reply in the SAME language the user uses: a Chinese question gets a Chinese \
reply, an English question an English reply.
- Never mention tool names or internal implementation details to the user.
"""

# --- server-side tool execution (import store directly, no HTTP hop) ----------


def _exec_tool(name, args):
    args = args or {}
    print(f"[chat] tool call: {name} args={json.dumps(args, default=str)[:300]}")
    if name == "get_home_briefing":
        b = store.get_briefing(args.get("time_range_days", 30))
        return _jsonable(b or {"summary": "No briefing available.", "items": []})
    if name == "get_appliance":
        matches = store.find_appliances(args["name"])
        if len(matches) != 1:
            cands = [
                {"appliance_id": a.get("appliance_id"), "label": store._label(a)}
                for a in (matches or store.get_appliances())
            ]
            return {
                "status": "needs_clarification",
                "summary": f"'{args['name']}' matches {'several appliances' if matches else 'nothing'}. "
                           "Which one did you mean?",
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
            {"kind": a.get("kind"), "title": a.get("title"),
             "severity": a.get("severity"), "source_url": a.get("source_url")}
            for a in store.get_alerts()
            if a.get("appliance_id") == appl["appliance_id"] and not a.get("read")
        ]
        warr_txt = (
            f"under warranty until {warranty['warranty_end']}"
            if warranty["status"] == "active"
            else ("warranty expired" if warranty["status"] == "expired" else "warranty unknown")
        )
        return _jsonable({
            "summary": (
                f"{store._label(appl)}: {warr_txt}, {store.device_age(appl)} old. "
                f"{len(tasks)} maintenance tasks on file, {len(alerts)} open alerts."
            ),
            "appliance": {
                "appliance_id": appl.get("appliance_id"),
                "label": store._label(appl),
                "category": appl.get("category"),
                "brand": appl.get("brand"),
                "model": appl.get("model"),
                "room": appl.get("room"),
                "install_date": appl.get("install_date"),
                "device_age": store.device_age(appl),
            },
            "warranty": warranty,
            "maintenance_history": [
                {"task": t["task"], "last_done": t.get("last_done"),
                 "next_due": t.get("next_due"), "urgency": t.get("urgency")}
                for t in tasks
            ],
            "alerts": alerts,
        })
    if name == "add_appliance":
        try:
            res = store.add_appliance(
                args["category"], args.get("brand"), args.get("model"),
                args.get("room"), args.get("install_date"),
            )
        except ValueError as exc:
            return {"status": "error", "summary": str(exc)}
        appl = res["appliance"]
        return _jsonable({
            "summary": f"Added {store._label(appl)} with {len(res['tasks_created'])} scheduled maintenance tasks.",
            "appliance_id": appl["appliance_id"],
            "label": store._label(appl),
            "warranty_end": appl.get("warranty_end"),
            "tasks": [{"task": t["task"], "next_due": t["next_due"]} for t in res["tasks_created"]],
        })
    if name == "start_photo_onboarding":
        tok = store.create_upload_token()
        # REAL upload URL (CloudFront), not the example.com placeholder in src/server.py.
        upload_url = f"{UPLOAD_PAGE_BASE}?token={tok['token']}"
        return {
            "summary": ("Photo onboarding started. The user can open the upload link on their "
                        "phone, photograph the appliance nameplate, and I'll build the record automatically."),
            "upload_url": upload_url,
            "qr_text": upload_url,
            "expires_in_seconds": tok["expires_in_seconds"],
            "card_title": "Add an appliance",
            "card_body": "Scan to photograph the nameplate. The link expires in 15 minutes.",
        }
    if name == "log_maintenance":
        try:
            res = store.log_maintenance(args["appliance"], args["task"], args["action"], args.get("date"))
        except store.ApplianceLookupError as exc:
            return {"status": "needs_clarification", "summary": str(exc), "candidates": exc.candidates}
        except ValueError as exc:
            return {"status": "error", "summary": str(exc)}
        when = "completed" if res["action"] == "done" else "snoozed for 30 days"
        return _jsonable({
            "summary": f"Marked '{res['task']['task']}' as {when}. Next due {res['next_due']}.",
            "next_due": res["next_due"], "action": res["action"], "task": res["task"]["task"],
        })
    if name == "get_reorder_options":
        res = store.get_reorder_options(args["appliance_or_consumable"])
        if not res["options"]:
            return {"status": "not_found",
                    "summary": f"I couldn't find a consumable matching '{args['appliance_or_consumable']}'. "
                               "Try naming the appliance, like 'fridge filter' or 'smoke alarm battery'."}
        first = res["options"][0]
        return _jsonable({
            "summary": (f"Found {len(res['options'])} option(s). Top pick: {first['consumable']} "
                        f"({first['spec']}). I've marked the related reminder as handled."),
            "options": res["options"],
        })
    if name == "report_issue":
        try:
            rep = store.get_issue_report(args["appliance"], args["symptom"])
        except store.ApplianceLookupError as exc:
            return {"status": "needs_clarification", "summary": str(exc), "candidates": exc.candidates}
        w = rep["warranty"]
        warr_txt = (
            f"under warranty until {w['warranty_end']}"
            if w["status"] == "active"
            else ("warranty expired" if w["status"] == "expired" else "warranty unknown")
        )
        tri = rep["triage"]
        tri_txt = (f"Catalog guidance for '{tri['symptom']}': {tri['note']}" if tri
                   else "No direct catalog match for this symptom.")
        return _jsonable({
            "summary": f"{rep['label']}: {warr_txt}, {rep['device_age']} old. {tri_txt}",
            "appliance": {k: rep["appliance"].get(k) for k in
                           ("appliance_id", "category", "brand", "model", "room", "install_date")},
            "label": rep["label"], "device_age": rep["device_age"], "warranty": w,
            "last_maintenance": rep["last_maintenance"], "open_tasks": rep["open_tasks"],
            "alerts": rep["alerts"], "triage": tri, "symptom": rep["symptom"],
        })
    raise ValueError(f"unknown tool: {name}")


def _chat(message, history):
    messages = []
    for h in history or []:
        messages.append({"role": h["role"], "content": [{"text": h["text"]}]})
    messages.append({"role": "user", "content": [{"text": message}]})

    tool_calls = []
    last_text = ""
    for _ in range(5):  # up to 5 tool-loop iterations
        resp = _bedrock().converse(
            modelId=MODEL_ID,
            system=[{"text": SYSTEM_PROMPT}],
            messages=messages,
            toolConfig={"tools": TOOLS},
            inferenceConfig={"maxTokens": 800, "temperature": 0.3},
        )
        out = resp["output"]["message"]
        messages.append(out)
        last_text = " ".join(
            b.get("text", "") for b in out.get("content", []) if "text" in b
        )
        calls = [b["toolUse"] for b in out.get("content", []) if "toolUse" in b]
        if not calls:
            break
        results = []
        for c in calls:
            try:
                res = _exec_tool(c["name"], c.get("input"))
            except Exception as exc:  # noqa: BLE001 - never break the chat on a tool error
                res = {"status": "error", "summary": f"Tool {c['name']} failed: {exc}"}
            res = _jsonable(res)
            tool_calls.append({"name": c["name"], "result": res})
            results.append({
                "toolResult": {
                    "toolUseId": c["toolUseId"],
                    # Converse requires content as a LIST of content blocks
                    "content": [{"json": res}],
                }
            })
        messages.append({"role": "user", "content": results})
    return last_text.strip(), tool_calls


# --- nameplate OCR --------------------------------------------------------------

def _strict_fields(raw: dict) -> dict:
    """Coerce model output to the strict {brand, model, serial_number,
    manufacture_date} x {value, confidence} dict."""
    out = {}
    for key in ("brand", "model", "serial_number", "manufacture_date"):
        entry = (raw or {}).get(key) or {}
        value = entry.get("value")
        try:
            conf = float(entry.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        if value is not None:
            value = str(value).strip() or None
        out[key] = {"value": value, "confidence": conf}
    return out


def _bedrock_extract_nameplate(image_bytes: bytes) -> dict:
    """Extract nameplate fields via Bedrock Claude vision.

    FIRST REAL IMPLEMENTATION of agent/home_analyst.py::bedrock_extract_nameplate
    (which still raises NotImplementedError). Uses the SAME NAMEPLATE_PROMPT and
    NAMEPLATE_SCHEMA constants (imported, not copied), forcing the model to
    answer through a Converse tool call whose input schema is the schema.
    """
    fmt = "png" if image_bytes[:8] == b"\x89PNG\r\n\x1a\n" else "jpeg"
    tool = {
        "toolSpec": {
            "name": "extract_nameplate",
            "description": "Return the nameplate fields as JSON matching the schema.",
            "inputSchema": {"json": NAMEPLATE_SCHEMA},
        }
    }
    print(f"[ocr] calling bedrock vision, image {len(image_bytes)} bytes, format={fmt}")
    resp = _bedrock().converse(
        modelId=MODEL_ID,
        system=[{"text": "You extract appliance nameplate data exactly as printed. "
                         "Never invent values; use null for anything not legible."}],
        messages=[{
            "role": "user",
            "content": [
                {"image": {"format": fmt, "source": {"bytes": image_bytes}}},
                {"text": NAMEPLATE_PROMPT},
            ],
        }],
        toolConfig={"tools": [tool], "toolChoice": {"tool": {"name": "extract_nameplate"}}},
        inferenceConfig={"maxTokens": 1000, "temperature": 0.0},
    )
    for block in resp["output"]["message"].get("content", []):
        if "toolUse" in block:
            print(f"[ocr] bedrock tool input: {json.dumps(block['toolUse'].get('input'), default=str)[:400]}")
            return _strict_fields(block["toolUse"].get("input"))
    raise RuntimeError("bedrock returned no extract_nameplate tool call")


def _synthetic_fallback(image_bytes: bytes) -> dict | None:
    """Deterministic fallback, SYNTHETIC DEMO PATH ONLY.

    The demo test renders its own nameplate with PIL and embeds the expected
    fields in the PNG's tEXt chunk under the key "homekeeper_synthetic" (a
    JSON {brand, model, serial_number, manufacture_date} x {value, confidence}
    dict). This fallback fires ONLY when (a) the Bedrock vision call raised,
    and (b) that chunk is present. It never invents values from a real photo:
    a real nameplate has no such chunk, so real images can never take this path.
    The response carries "synthetic_fallback": true so it is never mistaken
    for real OCR. Honest, documented, demo-only.
    """
    try:
        from PIL import Image, PngImagePlugin  # noqa: F401 - PNG chunk parsing below
    except ImportError:
        return None
    try:
        import io
        im = Image.open(io.BytesIO(image_bytes))
        meta = im.info.get("homekeeper_synthetic")
        if not meta:
            return None
        raw = json.loads(meta)
        fields = _strict_fields(raw)
        fields["synthetic_fallback"] = True
        print("[ocr] synthetic demo fallback used (chunk homekeeper_synthetic present)")
        return fields
    except Exception:  # noqa: BLE001 - corrupt image / bad chunk -> no fallback
        return None


def _extract_nameplate(s3_key: str) -> tuple:
    """Fetch image bytes from S3 and extract; returns (status, fields dict)."""
    s3 = boto3.client("s3", region_name="us-east-1")
    obj = s3.get_object(Bucket=UPLOADS_BUCKET, Key=s3_key)
    image_bytes = obj["Body"].read()
    try:
        fields = _bedrock_extract_nameplate(image_bytes)
        fields["synthetic_fallback"] = False
        status = 200
    except Exception as exc:  # noqa: BLE001 - bedrock failure -> gated fallback
        print(f"[ocr] bedrock vision failed ({type(exc).__name__}): {exc}")
        fb = _synthetic_fallback(image_bytes) if SYNTHETIC_KEY_MARKER in s3_key else None
        if fb is None:
            return 502, {"error": "bedrock_unavailable", "detail": str(exc)}
        fields, status = fb, 200
    # Catalog classification for photo onboarding (heuristic fallback from
    # agent/home_analyst.py; the production agent refines it via LLM).
    brand = (fields.get("brand") or {}).get("value") or ""
    model = (fields.get("model") or {}).get("value") or ""
    fields["suggested_category"] = _classify_category(f"{brand} {model}", store.get_catalog())
    return status, fields


def _check_upload(s3_key: str) -> dict:
    """S3 HeadObject -> {"uploaded": bool} (false on 404 / missing)."""
    s3 = boto3.client("s3", region_name="us-east-1")
    try:
        s3.head_object(Bucket=UPLOADS_BUCKET, Key=s3_key)
        return {"uploaded": True}
    except ClientError as exc:
        code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        print(f"[check_upload] {s3_key}: head_object failed ({type(exc).__name__}, http={code})")
        return {"uploaded": False}
    except Exception as exc:  # noqa: BLE001 - defensive; report as not uploaded
        print(f"[check_upload] {s3_key}: unexpected error ({type(exc).__name__}): {exc}")
        return {"uploaded": False}


# --- handler ----------------------------------------------------------------------

def handler(event, context):
    if (event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS"
            or event.get("httpMethod") == "OPTIONS"):
        return _resp(200, {"ok": True})

    try:
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            import base64

            body = base64.b64decode(body).decode("utf-8")
        payload = json.loads(body)
    except Exception:
        return _resp(400, {"error": "invalid JSON body"})

    action = payload.get("action")
    try:
        if action == "health":
            return _resp(200, {"ok": True, "model": MODEL_ID})
        if action == "chat":
            message = (payload.get("message") or "").strip()
            if not message:
                return _resp(400, {"error": "missing message"})
            reply, tool_calls = _chat(message, payload.get("history") or [])
            return _resp(200, {"reply": reply, "tool_calls": tool_calls})
        if action == "extract_nameplate":
            s3_key = (payload.get("s3_key") or "").strip()
            if not s3_key:
                return _resp(400, {"error": "missing s3_key"})
            status, fields = _extract_nameplate(s3_key)
            return _resp(status, fields)
        if action == "check_upload":
            s3_key = (payload.get("s3_key") or "").strip()
            if not s3_key:
                return _resp(400, {"error": "missing s3_key"})
            return _resp(200, _check_upload(s3_key))
        return _resp(400, {"error": f"unknown action: {action!r}"})
    except Exception as exc:  # noqa: BLE001 - 502 with detail, never a 500 stack trace
        print(f"[handler] action={action} failed: {type(exc).__name__}: {exc}")
        return _resp(502, {"error": "upstream_failure", "detail": str(exc)})
