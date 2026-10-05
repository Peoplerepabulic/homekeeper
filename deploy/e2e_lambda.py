"""End-to-end test of the deployed Lambda via `aws lambda invoke`,
synthesizing Function URL (payload 2.0) events. Exercises the full path:
Mangum -> FastMCP streamable HTTP -> store -> DynamoDB. Also times calls.
"""
import base64
import json
import os
import subprocess
import sys
import time

FN = "homekeeper-mcp"
PROFILE = "alexa-ai-user"
REGION = "us-east-1"
HOST = "ikwwry36o6eftmfibyzymv5thy0kplnb.lambda-url.us-east-1.on.aws"


def invoke(method, params, rid, timeout_note=""):
    body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                       "params": params or {}})
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/mcp",
        "rawQueryString": "",
        "headers": {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "host": HOST,
        },
        "requestContext": {
            "accountId": "955857822289",
            "apiId": "ikwwry36o6eftmfibyzymv5thy",
            "domainName": HOST,
            "domainPrefix": "ikwwry36o6eftmfibyzymv5thy0kplnb",
            "http": {
                "method": "POST",
                "path": "/mcp",
                "protocol": "HTTP/1.1",
                "sourceIp": "1.2.3.4",
                "userAgent": "e2e-test",
            },
            "requestId": f"e2e-{rid}",
            "routeKey": "$default",
            "stage": "$default",
            "time": "05/Oct/2026:00:30:00 +0000",
            "timeEpoch": 1759624200000,
        },
        "body": body,
        "isBase64Encoded": False,
    }
    with open("/tmp/e2e-event.json", "w") as f:
        json.dump(event, f)
    t0 = time.monotonic()
    p = subprocess.run(
        ["aws", "lambda", "invoke", "--function-name", FN,
         "--cli-binary-format", "raw-in-base64-out",
         "--payload", "file:///tmp/e2e-event.json", "/tmp/e2e-out.json",
         "--profile", PROFILE, "--region", REGION],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "AWS_PROFILE": PROFILE, "AWS_REGION": REGION},
    )
    dt = (time.monotonic() - t0) * 1000
    if p.returncode != 0:
        return {"_error": p.stderr[:300]}, dt
    out = json.load(open("/tmp/e2e-out.json"))
    raw = out.get("body", "")
    if out.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    payload = raw
    for line in raw.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            break
    try:
        return json.loads(payload), dt
    except Exception:  # noqa: BLE001
        return {"_raw": raw[:400], "statusCode": out.get("statusCode")}, dt


def main():
    rid = 1
    init, dt = invoke("initialize", {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "e2e", "version": "1.0"}}, rid)
    rid += 1
    print(f"initialize: {dt:.0f}ms")
    if "_error" in init or "_raw" in init:
        print("  FAIL:", json.dumps(init)[:400])
        return
    print("  server:", init.get("result", {}).get("serverInfo"))

    tools, dt = invoke("tools/list", {}, rid)
    rid += 1
    names = [t["name"] for t in tools.get("result", {}).get("tools", [])]
    print(f"tools/list: {dt:.0f}ms count={len(names)}")
    print("  tools:", names)

    call, dt = invoke("tools/call",
                      {"name": "get_home_briefing", "arguments": {}}, rid)
    rid += 1
    print(f"tools/call get_home_briefing: {dt:.0f}ms")
    try:
        text = call["result"]["content"][0]["text"]
        data = json.loads(text)
        items = data.get("items", [])
        print("  summary:", data.get("summary", "")[:90])
        print("  items:", len(items),
              "| urgencies:", [i.get("urgency") for i in items])
        print("  types:", [i.get("type") for i in items])
    except Exception as e:  # noqa: BLE001
        print("  PARSE ISSUE:", str(e)[:150], json.dumps(call)[:400])

    # one more tool for breadth: report_issue (reads warranty/tasks/alerts)
    call2, dt2 = invoke("tools/call", {"name": "report_issue", "arguments": {
        "appliance": "water heater", "symptom": "strange noise"}}, rid)
    try:
        text2 = call2["result"]["content"][0]["text"]
        d2 = json.loads(text2)
        print(f"tools/call report_issue: {dt2:.0f}ms")
        print("  summary:", d2.get("summary", "")[:110])
    except Exception as e:  # noqa: BLE001
        print("  PARSE ISSUE:", str(e)[:150], json.dumps(call2)[:300])


if __name__ == "__main__":
    main()
