# AWS Deploy I — DynamoDB table + Lambda MCP server (2026-10-04)

Region: `us-east-1`. Profile: `alexa-ai-user` (IAM user `alexa-ai-tools`,
account 955857822289). All commands below need
`AWS_PROFILE=alexa-ai-user AWS_REGION=us-east-1`.
For CloudFront/global calls add `--cli-read-timeout 300` (the VM proxy makes
the default 60 s read timeout flaky; with 300 s the calls succeed in ~2 s).

---

# AWS Deploy II — S3/CloudFront web + presign Lambda (2026-10-04)

## What was created

| Resource | Name / value |
|---|---|
| S3 bucket (web) | `homekeeper-web-955857822289` — policy allows only the CloudFront OAC |
| CloudFront OAC | `EAZWT4H1DWNWL` |
| CloudFront distribution | `E2WW46N7SQVZRL` → `https://d250h79d4lokny.cloudfront.net/` (Deployed; DefaultRootObject `upload.html`; PriceClass_100; CachingDisabled so demo edits show immediately) |
| Web files | `web/index.html` (demo console placeholder), `web/upload.html` (wired to the presign endpoint) |
| S3 bucket (uploads) | `homekeeper-uploads-955857822289` — CORS allows PUT from the CloudFront origin only; lifecycle expires `uploads/*` after 7 days |
| Lambda function | `homekeeper-presign` — python3.12, 256 MB, 15 s timeout (`deploy/presign_lambda.py`) |
| Function URL | `https://4y52ctpkwpgzyf73545z4tw4li0ahghj.lambda-url.us-east-1.on.aws/` (auth `NONE`; the UPLOAD# token is the auth) |
| IAM role | `homekeeper-presign-role` — `AWSLambdaBasicExecutionRole` + inline: `dynamodb:GetItem` on `homekeeper`, `s3:PutObject` on `homekeeper-uploads-955857822289/uploads/*` |
| Env vars | `HOMEKEEPER_TABLE=homekeeper`, `UPLOADS_BUCKET=homekeeper-uploads-955857822289`, `DEMO_USER_ID=demo-household-1` |

## Presign flow

`start_photo_onboarding` (hot path) mints `UPLOAD#<token>` (15-min TTL) →
phone opens `https://d250h79d4lokny.cloudfront.net/upload.html?token=<token>` →
page GETs presign `?token=` → Lambda checks the row exists + `ttl > now` →
returns `{upload_url, s3_key}` → page PUTs the photo straight to S3.
Verified: `python3 deploy/verify_presign.py` (real token → 200, bogus → 403,
missing → 400). CloudFront serves the page with HTTP 200 (verified via curl).

## Gotchas hit (also in FRICTION_LOG.md)

6. **Python name-shadowing**: a module-level `_s3 = None` cache plus
   `def _s3():` rebinds the name to the function, so the first call returns
   the function itself (`'function' object has no attribute
   'generate_presigned_url'`). Only the valid-token path touched S3, so
   403/400 tests passed while 200 failed. Fix: separate cache name
   (`_s3_client`).
7. **Mangum synthetic events need the full `requestContext.http`**
   (incl. `sourceIp`) — the trimmed event raised `KeyError: 'sourceIp'`
   inside `mangum/handlers/api_gateway.py`.

## AgentCore — NOT deployed (blocked, reported to parent)

- The AgentCore control-plane API (`bedrock-agentcore-control`) IS reachable
  in us-east-1 and `ListAgentRuntimes` works (empty) — no account/region block.
- Blocker is environmental: **no Docker on the build VM** and `apt-get`
  crawls behind the proxy, so the standard `agentcore deploy` container-build
  path is unavailable. Per instructions, did not grind on it.
- Recommendation: run `daily_scan` as a plain Lambda + EventBridge Scheduler
  instead — it is 100% deterministic (no LLM needed), cheaper and simpler.
  Reserve AgentCore/Strands for photo-onboarding Task 1 when Bedrock vision
  is actually wired (Phase 3). Awaiting parent decision before implementing.

## Re-verify after any code change

```
python3 deploy/e2e_lambda.py   # initialize -> tools/list -> tools/call (via lambda:Invoke)
python3 deploy/verify_presign.py  # presign 200/403/400
```

---

## Deploy I details (original section follows)

## What was created

| Resource | Name / value |
|---|---|
| DynamoDB table | `homekeeper` — pk `pk` (S), sk `sk` (S), `PAY_PER_REQUEST`, no GSIs |
| Lambda function | `homekeeper-mcp` — python3.12, 512 MB, 30 s timeout |
| Lambda version | `2` (v1 = first broken build, v2 = working) |
| Function URL | `https://ikwwry36o6eftmfibyzymv5thy0kplnb.lambda-url.us-east-1.on.aws/` (auth `NONE`) |
| IAM role | `homekeeper-lambda-role` — `AWSLambdaBasicExecutionRole` + inline DynamoDB policy scoped to the `homekeeper` table only |
| Env vars | `HOMEKEEPER_TABLE=homekeeper`, `DEMO_CLOCK=2026-10-04` (`AWS_REGION` is reserved by Lambda — do NOT set it; the runtime provides it) |
| Seed | 36 items written via `python src/seed.py` (8 appliances, 24 tasks, 1 recall alert, 1 briefing, 1 profile, 1 event) |

## Packaging (zip route — no docker on the build VM)

```
pip install --target package/ "fastmcp>=4" mangum
cp src/*.py src/catalog.json lambda_handler.py package/
cd package && zip -r ../homekeeper-lambda.zip .   # DO NOT exclude *.dist-info
```

`lambda_handler.py` wraps `mcp.http_app(path="/mcp", stateless_http=True,
host_origin_protection=False)` with `Mangum(app, lifespan="auto")`.
Handler: `lambda_handler.handler`. Zip is ~17 MB.

## Gotchas hit (also in FRICTION_LOG.md)

1. **`*.dist-info` must stay in the zip.** Excluding it breaks the import:
   `Runtime.ImportModuleError: No package metadata was found for fastmcp`.
2. **FastMCP 4.x DNS-rebinding guard 403s the Function URL hostname.**
   Fix: `host_origin_protection=False` in `http_app()` (safe here — public
   HTTPS endpoint called server-to-server, not a localhost server).
3. **Mangum needs `lifespan="auto"`, not `"off"`.** FastMCP 4.x creates its
   StreamableHTTP session manager inside the ASGI lifespan; with `"off"`
   every request fails with `RuntimeError: Task group is not initialized`.
4. **Transient `aws` CLI timeouts** on this VM (proxy). Table/role creation
   calls timed out client-side but succeeded server-side — always
   re-check with describe/get before retrying blindly.
5. **The VM proxy 403s `*.lambda-url.*` hosts.** `curl` against the Function
   URL from here returns proxy 403s — a red herring, not an AWS problem.
   Verify via `aws lambda invoke` with synthetic Function URL (payload 2.0)
   events instead: see `e2e_lambda.py`. (Direct egress fails SSL — do not
   unset the proxy.)

## Not done / known limitations

- **Provisioned concurrency = 1 is BLOCKED**: new-account Lambda concurrency
  limit is 10, and allocating any provisioned concurrency would push
  unreserved below the 10 minimum. Raising it needs an AWS Support case
  (the Service Quotas API refuses: default quota is 1000). Mitigation for
  the demo: warm the function with one call ~1 min before recording; cold
  start is ~2.3 s, warm calls are 13–76 ms.
- **Warm latency** (from CloudWatch REPORT): 13–76 ms per tool call —
  comfortably under the 500 ms Alexa+ budget. Cold start ~2.3 s.

## Re-verify after any code change

```
python3 deploy/e2e_lambda.py   # initialize -> tools/list -> tools/call (via lambda:Invoke)
```
