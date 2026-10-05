# AWS Deploy I — DynamoDB table + Lambda MCP server (2026-10-04)

Region: `us-east-1`. Profile: `alexa-ai-user` (IAM user `alexa-ai-tools`,
account 955857822289). All commands below need
`AWS_PROFILE=alexa-ai-user AWS_REGION=us-east-1`.
For CloudFront/global calls add `--cli-read-timeout 300` (the VM proxy makes
the default 60 s read timeout flaky; with 300 s the calls succeed in ~2 s).

---

# Echo Show Simulator + homekeeper-chat (2026-10-04/05)

Simulated Alexa+ web app (user picked Option A). Only the Alexa+ client is
simulated; every data call hits the real AWS backend.

## Connectivity decision (tested, not guessed)

**Browser → MCP Function URL directly** for the 3 deterministic demo flows
(briefing / triage / photo onboarding). Verified via synthetic
`aws lambda invoke` (payload 2.0) events: initialize → tools/list (7 tools)
→ tools/call works statelessly, no session affinity needed. The page embeds
a ~40-line MCP client: POST `Accept: application/json, text/event-stream`,
parse the first SSE `data:` line.
**BFF via `homekeeper-chat` Lambda** for free-ask (Bedrock) + nameplate OCR
(Bedrock vision) + upload-status polling — things a browser can't do.

## Changes for the simulator

- **MCP Function URL CORS**: was `null` (browsers blocked). Set via
  `update-function-url-config` to
  `AllowOrigins=[https://d250h79d4lokny.cloudfront.net]`,
  `AllowMethods=["*"]`, `AllowHeaders=[content-type, accept,
  mcp-session-id, mcp-protocol-version]`. Gotcha: `AllowMethods` members
  must be ≤6 chars — `"OPTIONS"` (7) is rejected; the Lambda service
  auto-handles OPTIONS preflight, so it isn't needed in the list.
- **src/server.py `start_photo_onboarding`**: returned a
  `https://example.com/upload/<token>` placeholder (blueprint TODO). Now
  returns the real URL from `UPLOAD_PAGE_URL` env
  (`https://d250h79d4lokny.cloudfront.net/upload.html?token=<token>`).
  MCP Lambda code + env redeployed and re-verified (real token in URL).
- **web/index.html** (47 KB, single file, no build step): Echo Show chrome
  (Alexa light bar with listening/thinking/speaking states, voice waveform
  via Web Speech API + speechSynthesis with graceful fallback), conversation
  thread, 4 card types from docs/mcp-apps-design-notes.md (briefing with
  tap-to-detail, appliance detail, reorder, QR). Cards show
  `via MCP · <tool> · <ms>ms ✓ <500ms` provenance. QR via vendored
  qrcode-generator 1.4.4 (inlined, no external fetch). Honest label:
  "Simulated Alexa+ experience · Real MCP backend on AWS".
- Demo flows: (a) 早安简报 → get_home_briefing → 5 real items;
  (b) 故障分诊 → report_issue(water heater, strange noise) → triage card
  with rule-based recommendation; (c) 拍照建档 → start_photo_onboarding →
  QR card → poll `check_upload` (3 s) → `extract_nameplate` → fields card
  (confidence-flagged) → confirm → MCP `add_appliance` (real write).
  Free-ask → chat Lambda; tool results re-render as cards.

---

# AWS Deploy IV — `homekeeper-chat` Lambda: Bedrock chat + nameplate OCR (2026-10-04/05)

| Resource | Name / value |
|---|---|
| Lambda function | `homekeeper-chat` — python3.12, 512 MB, 60 s, x86_64 (`deploy/chat_lambda.py`) |
| Function URL | `https://g3smvyukhw25n2fauqdcx3pq7a0nmjxv.lambda-url.us-east-1.on.aws/` (auth `NONE`) |
| IAM role | `homekeeper-chat-role` — `AWSLambdaBasicExecutionRole` + inline: `dynamodb:GetItem/PutItem/Query/UpdateItem` on `homekeeper` only; `s3:GetObject` on `homekeeper-uploads-955857822289/uploads/*`; `bedrock:InvokeModel` on the Sonnet 4.5 foundation-model ARNs (us-east-1/2, us-west-2) + the `us.` inference-profile ARN |
| Env vars | `HOMEKEEPER_TABLE=homekeeper`, `DEMO_CLOCK=2026-10-04`, `UPLOADS_BUCKET=homekeeper-uploads-955857822289`, `DEMO_USER_ID=demo-household-1` |
| Model | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` (newest Sonnet in us-east-1 per `bedrock ListFoundationModels`; the bare foundation-model id rejects on-demand invocation, so the `us.` inference-profile id is used) |

## Actions (POST JSON; CORS `*`, OPTIONS handled in-code like presign_lambda)

- `chat` `{message, history:[{role,text}]}` → `{"reply", "tool_calls":[{name, result}]}`.
  Bedrock Converse with all 7 MCP tools defined; up to 5 tool-loop iterations.
  Tools execute server-side by importing `src/store.py` directly (no HTTP hop);
  the FULL parsed tool result is returned per call so the web UI renders
  briefing/reorder/triage cards. System prompt: warm Alexa-like HomeKeeper
  assistant, tools for all facts, never invents appliance data, replies in the
  user's language (Chinese → Chinese).
- `extract_nameplate` `{s3_key}` → `{brand:{value,confidence}, model, serial_number, manufacture_date, suggested_category, synthetic_fallback}`.
  **First real implementation of `agent/home_analyst.py::bedrock_extract_nameplate`**
  (still raises NotImplementedError there): same `NAMEPLATE_PROMPT` +
  `NAMEPLATE_SCHEMA` constants (imported, not copied), forced tool call via
  Converse vision. `suggested_category` from `_classify_category` (imported)
  on brand+model text.
- `check_upload` `{s3_key}` → `{"uploaded": bool}` via S3 HeadObject (false on 404).
- `health` → `{"ok": true, "model": "<id>"}`.

`start_photo_onboarding` returns the REAL upload URL
`https://d250h79d4lokny.cloudfront.net/upload.html?token=<token>`
(not the example.com placeholder in src/server.py).

## Packaging

Zip preserves repo layout (`chat_lambda.py` at root, `src/` with
db.py/store.py/scheduling.py/seed.py/catalog.json, `agent/home_analyst.py`);
boto3 from the runtime; Pillow vendored via `pip install --target` (only for
the synthetic-demo OCR fallback below).

## Verified 2026-10-05 (via `aws lambda invoke`, synthetic payload-2.0 events)

- `health` → 200; OPTIONS → 200 with CORS headers; `check_upload` bogus key →
  `{"uploaded": false}`, real uploaded key → `true`.
- OCR (real Bedrock vision): PIL-rendered synthetic Rheem nameplate
  (brand "Rheem", model "XE50M06ST45U1", serial "R123456789", mfg
  "2023-05-14", `uploads/synthetic-test-nameplate.png`) →
  all four fields exact, confidence 1.0, `synthetic_fallback: false`.
  CloudWatch `[ocr] bedrock tool input` shows the real extraction.
- Chat "what needs attention at home?" → `get_home_briefing` (real DynamoDB,
  CloudWatch `[chat] tool call`), reply lists all 5 briefing items
  (HVAC overdue, fridge recall, smoke alarm, purifier, water-heater warranty).
- Chat "my water heater is making a strange noise" → `report_issue`, reply
  mentions warranty (20 days left, expires 2026-10-24) + sediment-flush guidance.
- Chinese question → `get_appliance`, reply in Chinese (language rule works).

## Gotchas hit

9. **Cross-region inference profiles need `bedrock:InvokeModel` on the
   underlying foundation-model ARNs too** (here `us-east-2::foundation-model/…`;
   add us-west-2 as well). The profile ARN alone 403s.
10. **Converse `toolResult.content` must be a LIST of content blocks**
    (`[{"json": …}]`); passing a dict raises client-side ParamValidationError.

## Synthetic-demo OCR fallback (documented, gated, honest)

If Bedrock vision raises, `extract_nameplate` falls back ONLY when the s3 key
contains `synthetic` AND the PNG carries a `homekeeper_synthetic` tEXt chunk
(real photos never have it). The fallback returns that chunk's fields with
`"synthetic_fallback": true` — never mistaken for real OCR. It fired once
during testing (pre-IAM-fix AccessDenied); the passing test used real vision.

## Bedrock cost this session

~10 Converse calls total (incl. 2 failed-fast AccessDenied): vision + chat
tokens ≈ $0.30. All within "a handful" as instructed.

AgentCore fallback (parent decision): `daily_scan` is 100% deterministic
(recompute urgency + CPSC match + rewrite briefing, no LLM), so it runs as
a plain Lambda on a Scheduler cron instead of an AgentCore Runtime agent.
AgentCore/Strands is reserved for Task 1 photo-onboarding when Bedrock
vision is actually wired (Phase 3).

## What was created

| Resource | Name / value |
|---|---|
| Lambda function | `homekeeper-daily-scan` — python3.12, 1024 MB, 300 s timeout (`deploy/daily_scan_lambda.py`) |
| IAM role | `homekeeper-dailyscan-role` — `AWSLambdaBasicExecutionRole` + inline: `dynamodb:GetItem/PutItem/Query` on `homekeeper` only |
| Env vars | `HOMEKEEPER_TABLE=homekeeper`, `DEMO_CLOCK=2026-10-04` |
| EventBridge Scheduler | `homekeeper-daily-scan` — `cron(0 6 * * ? *)` in `America/Chicago`, `FlexibleTimeWindow OFF`, state `ENABLED` |
| Scheduler role | `homekeeper-scheduler-role` — trusts `scheduler.amazonaws.com`, `lambda:InvokeFunction` on the daily-scan function only |

## How it works

Handler reuses `agent/home_analyst.py::daily_scan` unchanged (injectable
design — the Lambda only supplies a `cpsc_fn`). The one Lambda-specific
piece is `_cached_cpsc_fn`: the full CPSC dump is ~28 MB, so per
(brand, model) match results are cached as `META#cpsc_match#<brand>#<model>`
rows (20 h TTL) instead of refetching on every invocation. Within one
run, `home_analyst`'s in-process 24 h cache already dedups the download
across the 8 appliances.

Zip: `requests` vendored via `pip install --target` (boto3 comes with the
runtime); repo layout preserved (`agent/`, `src/` + `catalog.json`);
handler `daily_scan_lambda.handler`.

## Verified 2026-10-04

Manual `aws lambda invoke` → 200:
`{"date": "2026-10-04", "tasks_updated": 0, "alerts_created": 0, "briefing_items": 5}` —
BRIEFING#latest recomputed as exactly the demo 5-item briefing
(overdue/high/due_soon/due_soon/due_soon), 6 `META#cpsc_match#…` cache rows
written, `EVENT#…/daily_scan` logged. `tasks_updated=0` / `alerts_created=0`
are correct: seed data is already current and the demo recall alert exists
(dedup working).

## Gotcha hit (also in FRICTION_LOG.md)

8. **boto3 rejects Python `float`.** Storing `time.time()` in the cache row
   raised `TypeError: Float types are not supported. Use Decimal types
   instead.` Fix: `int(time.time())` epoch seconds.

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
