"""Verify the homekeeper-presign Lambda end-to-end.

1. Call start_photo_onboarding on the MCP Lambda (via lambda:Invoke with a
   synthetic Function URL event) to mint a REAL upload token.
2. Invoke homekeeper-presign with ?token=<real>  -> expect 200 + upload_url.
3. Invoke with ?token=bogus                   -> expect 403.
4. Invoke with no token                        -> expect 400.
"""
import base64
import json
import os
import subprocess
import sys
import time

PROFILE = "alexa-ai-user"
REGION = "us-east-1"
MCP_FN = "homekeeper-mcp"
MCP_HOST = "ikwwry36o6eftmfibyzymv5thy0kplnb.lambda-url.us-east-1.on.aws"
PRESIGN_FN = "homekeeper-presign"
PRESIGN_HOST = "4y52ctpkwpgzyf73545z4tw4li0ahghj.lambda-url.us-east-1.on.aws"


def mcp_call(method, params, rid):
    body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                       "params": params or {}})
    event = {
        "version": "2.0", "routeKey": "$default", "rawPath": "/mcp",
        "rawQueryString": "",
        "headers": {"content-type": "application/json",
                    "accept": "application/json, text/event-stream",
                    "host": MCP_HOST},
        "requestContext": {
            "accountId": "955857822289",
            "apiId": "ikwwry36o6eftmfibyzymv5thy",
            "domainName": MCP_HOST,
            "domainPrefix": "ikwwry36o6eftmfibyzymv5thy0kplnb",
            "http": {"method": "POST", "path": "/mcp", "protocol": "HTTP/1.1",
                     "sourceIp": "1.2.3.4", "userAgent": "ps-verify"},
            "requestId": f"ps-{rid}", "routeKey": "$default",
            "stage": "$default",
            "time": "05/Oct/2026:00:45:00 +0000",
            "timeEpoch": 1759625100000,
        },
        "body": body, "isBase64Encoded": False,
    }
    with open("/tmp/ps-event.json", "w") as f:
        json.dump(event, f)
    p = subprocess.run(
        ["aws", "lambda", "invoke", "--function-name", MCP_FN,
         "--cli-binary-format", "raw-in-base64-out",
         "--payload", "file:///tmp/ps-event.json", "/tmp/ps-out.json"],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "AWS_PROFILE": PROFILE, "AWS_REGION": REGION},
    )
    if p.returncode != 0:
        raise RuntimeError("mcp invoke failed: " + p.stderr[:300])
    out = json.load(open("/tmp/ps-out.json"))
    raw = out.get("body", "")
    if out.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    for line in raw.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise RuntimeError("no SSE data line: " + raw[:300])


def presign_call(token):
    event = {
        "version": "2.0", "routeKey": "$default", "rawPath": "/",
        "rawQueryString": f"token={token}" if token else "",
        "headers": {"host": PRESIGN_HOST},
        "queryStringParameters": {"token": token} if token else {},
        "requestContext": {"http": {"method": "GET", "path": "/"}},
        "isBase64Encoded": False,
    }
    with open("/tmp/ps-event2.json", "w") as f:
        json.dump(event, f)
    t0 = time.monotonic()
    p = subprocess.run(
        ["aws", "lambda", "invoke", "--function-name", PRESIGN_FN,
         "--cli-binary-format", "raw-in-base64-out",
         "--payload", "file:///tmp/ps-event2.json", "/tmp/ps-out2.json"],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "AWS_PROFILE": PROFILE, "AWS_REGION": REGION},
    )
    dt = (time.monotonic() - t0) * 1000
    if p.returncode != 0:
        return {"_error": p.stderr[:300]}, dt
    out = json.load(open("/tmp/ps-out2.json"))
    return {"status": out.get("statusCode"),
            "body": json.loads(out.get("body", "{}"))}, dt


def main():
    mcp_call("initialize", {"protocolVersion": "2025-11-25",
                             "capabilities": {},
                             "clientInfo": {"name": "ps", "version": "1.0"}}, 1)
    res = mcp_call("tools/call", {"name": "start_photo_onboarding",
                                  "arguments": {}}, 2)
    text = res["result"]["content"][0]["text"]
    data = json.loads(text)
    token = data["upload_url"].rstrip("/").split("/")[-1]
    print("minted token:", token[:8] + "...")

    r, dt = presign_call(token)
    ok = r.get("status") == 200 and r.get("body", {}).get("upload_url", "").startswith("https://")
    print(f"valid token: {dt:.0f}ms -> {r.get('status')} {'PASS' if ok else 'FAIL: ' + json.dumps(r)[:200]}")
    if ok:
        print("  s3_key:", r["body"]["s3_key"])

    r2, _ = presign_call("bogus-token-xyz")
    print("bogus token:", r2.get("status"), "PASS" if r2.get("status") == 403 else "FAIL")

    r3, _ = presign_call("")
    print("missing token:", r3.get("status"), "PASS" if r3.get("status") == 400 else "FAIL")

    if not (ok and r2.get("status") == 403 and r3.get("status") == 400):
        sys.exit(1)
    print("ALL PRESIGN CHECKS PASS")


if __name__ == "__main__":
    main()
