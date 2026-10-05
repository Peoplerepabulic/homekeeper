"""Smoke-test the deployed HomeKeeper MCP server over its Function URL.

Flow: initialize -> notifications/initialized -> tools/list -> tools/call.
Reports tool count, briefing item check, and per-call latency.
"""
import json
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://ikwwry36o6eftmfibyzymv5thy0kplnb.lambda-url.us-east-1.on.aws"
URL = BASE.rstrip("/") + "/mcp"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def rpc(method, params=None, rid=1):
    body = json.dumps(
        {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
    ).encode()
    req = urllib.request.Request(URL, data=body, headers=HEADERS, method="POST")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            dt = (time.monotonic() - t0) * 1000
            # Streamable HTTP may answer as SSE ("data: {...}") or plain JSON.
            payload = raw
            if raw.startswith("event:") or raw.startswith("data:"):
                for line in raw.splitlines():
                    if line.startswith("data:"):
                        payload = line[5:].strip()
                        break
            return json.loads(payload), dt, resp.status
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}, (time.monotonic() - t0) * 1000, 0


def main():
    rid = 1
    init, dt, st = rpc(
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "deploy-smoke", "version": "1.0"},
        },
        rid,
    )
    rid += 1
    print(f"initialize: http={st} latency={dt:.0f}ms")
    if "_error" in init:
        print("  ERROR:", init["_error"][:300])
        return
    print("  server:", init.get("result", {}).get("serverInfo"))

    # notifications/initialized (no id -> notification, response may be 202 empty)
    body = json.dumps(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    ).encode()
    req = urllib.request.Request(URL, data=body, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()
            print(f"notifications/initialized: http={resp.status}")
    except Exception as e:  # noqa: BLE001
        print("notifications/initialized error:", str(e)[:200])

    tools, dt, st = rpc("tools/list", {}, rid)
    rid += 1
    names = [t["name"] for t in tools.get("result", {}).get("tools", [])]
    print(f"tools/list: http={st} latency={dt:.0f}ms count={len(names)}")
    print("  tools:", names)

    call, dt, st = rpc(
        "tools/call",
        {"name": "get_home_briefing", "arguments": {}},
        rid,
    )
    print(f"tools/call get_home_briefing: http={st} latency={dt:.0f}ms")
    try:
        content = call["result"]["content"]
        text = content[0].get("text", "") if content else ""
        data = json.loads(text) if text else {}
        items = data.get("items", [])
        print("  summary:", data.get("summary", "")[:100])
        print("  items:", len(items), "| urgencies:", [i.get("urgency") for i in items])
        print("  types:", [i.get("type") for i in items])
    except Exception as e:  # noqa: BLE001
        print("  PARSE ISSUE:", str(e)[:200], "| raw:", json.dumps(call)[:400])


if __name__ == "__main__":
    main()
