# HomeKeeper — Product Feedback (for Devpost submission)

> Per the Build, Ship, Shape requirement: for every tool, API, or SDK used — what it was
> used for, what worked well, what needs work, onboarding (zero to hello world), and
> whether we'd build with it again, and why.

## 1. Alexa+ developer tools / Alexa AI CLI (track tool)

**Used for:** Intended as the primary client — we wanted a real Alexa+ skill/app as the
front end for HomeKeeper.

**What happened:** The Alexa AI CLI path was completely inaccessible. `alexa-ai` setup
requires assuming an Amazon-internal CodeArtifact role, and our account's
`sts:AssumeRole` is denied by an Amazon-side allowlist. There is no self-serve way to
request access; the failure is silent-ish (a bare AccessDenied on AssumeRole) with no
pointer to an access-request form.

**What needs work:** If Alexa+ wants hackathon builders, the Alexa AI toolchain needs a
public onboarding path — even a waitlist with a clear "request access" link would beat
a dead-end AssumeRole denial. We burned half a day proving the negative before pivoting.

**Zero to hello world:** Never reached hello world. Onboarding score: 0/10 through no
fault of the docs — the gate is access, not documentation.

**Build with it again?** Yes, instantly — if access were granted. We built a
high-fidelity Echo Show simulator precisely because we wanted the Alexa+ experience;
we'd replace the simulator with the real thing the day access opens.

## 2. Model Context Protocol (MCP) + FastMCP (Python)

**Used for:** The entire backend API — 7 tools (`get_home_briefing`, `get_appliance`,
`add_appliance`, `start_photo_onboarding`, `log_maintenance`, `get_reorder_options`,
`report_issue`) served via Streamable HTTP from AWS Lambda.

**What worked well:** The protocol design is excellent for this use case — one
`tools/call` route, JSON-RPC framing, and the browser client needed no SDK, just
`fetch`. FastMCP's `@mcp.tool()` decorator took us from Python function to MCP tool
in one line. Stateless HTTP mode was the right call for Lambda.

**What needs work (3 specific issues):**
- FastMCP 4.x's DNS-rebinding host guard rejects `*.lambda-url.*` hostnames with a
  bare 403 — the guard's threat model (localhost dev servers) doesn't apply to public
  HTTPS endpoints. The docs should call out the Lambda/Function-URL case and the
  `host_origin_protection=False` escape hatch.
- The StreamableHTTP session manager is only created inside the ASGI lifespan, so
  wrapping with Mangum requires `lifespan="auto"` — with `"off"` every request dies
  with `RuntimeError: Task group is not initialized`. No FastMCP+Mangum guide mentions
  this.
- FastMCP reads its own version via `importlib.metadata` at import time, so the common
  Lambda trick of stripping `*.dist-info` from the zip to save space causes
  `Runtime.ImportModuleError: No package metadata was found for fastmcp`.

**Zero to hello world:** ~30 minutes from `pip install fastmcp` to a working local
server. Excellent.

**Build with it again?** Absolutely. MCP is the reason our "simulated" frontend talks
to a genuinely real backend — the same tools serve the demo UI, the Bedrock chat
agent, and the daily scan.

## 3. AWS Lambda

**Used for:** All compute — MCP server, chat/vision, presigned-URL issuer, daily scan.

**What worked well:** Python 3.12 runtime, 512MB/30s config served the MCP hot path at
13–76ms warm. Deployment via zip is boring and reliable. CloudWatch Logs made the
Function-URL investigation possible.

**What needs work (2 specific issues):**
- **Lambda Function URLs returned 403 (`AccessDeniedException`) account-wide** —
  on a hello-world function, in two regions, unsigned and SigV4-signed, from inside
  AWS itself, with `AuthType: NONE` confirmed by `get-function-url-config`. Standalone
  account, no SCPs. Root cause unknown; looks like an account-level frontend problem
  on a brand-new account. We lost the better part of a day and migrated everything to
  API Gateway. A console-visible health check for the URL frontend would save others
  from this rabbit hole.
- New accounts get a Lambda concurrency limit of 10, so provisioned concurrency
  (our <500ms p95 plan) was rejected; the Service Quotas API refused the increase
  and pointed at a Support case. For hackathon timelines, document the warm-up-call
  workaround.

**Zero to hello world:** 10 minutes to first deployed function. Great.

**Build with it again?** Yes — it's the backbone of the project. But we'd budget a
spike on day one to verify Function URLs work on the account before designing around
them.

## 4. Amazon Bedrock (Claude Sonnet 4.5)

**Used for:** Free-ask chat (Converse API with MCP tool use, up to 5 iterations) and
nameplate OCR via vision.

**What worked well:** Tool calling just worked — a Chinese question about water-heater
warranty correctly triggered `get_appliance` and a clarification round. Vision
extracted brand/model/serial/manufacture-date from a synthetic nameplate at
confidence 1.0. The whole cold path cost ~$0.30 in testing.

**What needs work:** Nothing blocking. Inference profiles (`us.anthropic...`) are a
small papercut — the model ID format differs from the base model ID and the error
message when you use the wrong one is cryptic.

**Zero to hello world:** ~1 hour including IAM permissions. Good.

**Build with it again?** Yes. The hot-path/cold-path split (deterministic Lambda for
the demo flows, Bedrock for judgment calls) is the architecture we'd reuse.

## 5. Amazon DynamoDB

**Used for:** All state — appliances, tasks, onboarding tokens, CPSC recall cache
(single table, `pk`/`sk`, pay-per-request).

**What worked well:** Single-table design with `pk`/`sk` covered every access pattern
with no GSIs. Pay-per-request meant zero capacity planning for a demo.

**What needs work:** One gotcha — boto3's DynamoDB serializer rejects Python floats
anywhere in the item (`TypeError: Float types are not supported`), which bit us on a
cache timestamp. A float→Decimal sanitizer (or a lint) would be a nice touch.

**Zero to hello world:** 15 minutes. Great.

**Build with it again?** Yes.

## 6. Amazon API Gateway (HTTP API)

**Used for:** The rescue frontend — `/mcp*`, `/chat*`, `/presign*` routes with Lambda
proxy integrations and CORS, after Function URLs failed account-wide.

**What worked well:** Created the API, routes, and integrations in minutes; the first
POST returned 200 immediately where Function URLs had 403'd for hours. Method-specific
routes (not `ANY`) were needed so OPTIONS preflights return 204 instead of 405.

**What needs work:** The `ANY`-method footgun for CORS preflights deserves a console
warning when a browser-origin CORS config is present.

**Zero to hello world:** 20 minutes. Great.

**Build with it again?** Yes — it's now our default for browser-facing Lambda.

## 7. Amazon EventBridge Scheduler + S3 + CloudFront

**Used for:** Daily 6:00 AM CT CPSC recall scan; static site hosting and photo uploads.

**What worked well:** Scheduler cron with timezone support replaced a planned
AgentCore container (which needed Docker, unavailable on our build VM) with zero
regret — the scan is 100% deterministic. S3 + CloudFront + invalidation was uneventful
in the best way; presigned PUT URLs with a 7-day lifecycle covered the phone-upload
flow.

**What needs work:** AgentCore docs assume Docker on the build machine; a Docker-less
path (e.g., CodeBuild) for pure-Python agents would help locked-down environments.

**Build with it again?** Yes, all three.
