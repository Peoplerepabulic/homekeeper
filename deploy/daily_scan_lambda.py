"""Daily-scan Lambda (HomeKeeper cold path, AgentCore fallback).

Runs `agent/home_analyst.py::daily_scan` on an EventBridge Scheduler cron.
Reuses the injectable task function — no logic is duplicated here. The only
Lambda-specific piece is the CPSC caching wrapper: the full CPSC dump is
~28 MB, so per-(brand, model) match results are cached in DynamoDB
(`META#cpsc_match#…`, 20 h TTL) instead of refetching on every invocation.

Zip layout (repo structure preserved):
    daily_scan_lambda.py          <- this file, handler = daily_scan_lambda.handler
    agent/home_analyst.py
    src/db.py src/store.py src/scheduling.py src/catalog.json
    requests/  (only third-party dep; boto3 comes with the runtime)

Env: HOMEKEEPER_TABLE=homekeeper, DEMO_CLOCK=2026-10-04
"""

import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_ROOT, "agent"))
sys.path.insert(0, os.path.join(_ROOT, "src"))

import db  # noqa: E402  (DynamoDB single-table key schema)
import home_analyst  # noqa: E402  (cold-path task functions)

# Per-(brand, model) recall-match cache: skip the 28 MB CPSC download when a
# fresh result (< 20 h) is already in DynamoDB.
CPSC_MATCH_TTL = 20 * 3600


def _cached_cpsc_fn(brand: str, model: str) -> list:
    """cpsc_fn for daily_scan with a DynamoDB-backed 20 h result cache."""
    key = f"META#cpsc_match#{home_analyst._norm(brand)}#{home_analyst._norm(model)}"
    now = int(time.time())  # int, not float: boto3 rejects Python floats
    item = db.get_item(key)
    if item and now - int(item.get("at", 0)) < CPSC_MATCH_TTL:
        return item.get("matches", [])
    matches = home_analyst._default_cpsc_fn(brand, model)
    db.put_item(key, {
        "entity": "META",
        "brand": brand,
        "model": model,
        "matches": matches,
        "at": now,
    })
    return matches


def handler(event, context):
    """EventBridge Scheduler target. `today` comes from DEMO_CLOCK via db."""
    result = home_analyst.daily_scan(cpsc_fn=_cached_cpsc_fn)
    return {"statusCode": 200, "body": json.dumps(result, default=str)}
