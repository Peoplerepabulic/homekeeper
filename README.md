# HomeKeeper

*Every appliance remembered. Every task on time.*

**🎬 Live demo:** https://d250h79d4lokny.cloudfront.net/
*(Simulated Alexa+ experience · Real MCP backend on AWS)*

HomeKeeper is a home-appliance manager built for the Alexa+ track of the
*Build, Ship, Shape: Amazon Developer Hackathon*. It remembers every appliance's
model, maintenance schedule, warranty, and recall status — and tells you what needs
attention before you have to ask.

## Why a simulator?

Alexa's developer toolchain (Alexa AI CLI) was not accessible from our account —
setup requires assuming an Amazon-internal allowlist role, and `sts:AssumeRole` is
denied with no self-serve access path. Rather than fake it, we built a
high-fidelity **Echo Show web simulator** over a **100% real backend**: seven MCP
tools on AWS Lambda + DynamoDB, with Amazon Bedrock for chat and vision. The
simulator is labeled as simulated on screen; every card it renders comes from live
MCP tool calls.

The backend speaks native MCP (Streamable HTTP), so the day Alexa+ opens its
agentic APIs, these seven tools plug straight in — no rewrite.

## Architecture

The core decision is a **hot path / cold path split**:

- **Hot path** (every demo interaction): Echo Show simulator → API Gateway →
  Lambda (FastMCP, stateless) → DynamoDB single table. **No LLM on this path.**
  Warm latency 13–76 ms; target < 500 ms.
- **Cold path** (async): EventBridge daily 6:00 AM CT trigger → Lambda pulls the
  live CPSC recall feed → results cached in DynamoDB for the next briefing.
  Bedrock Claude handles free-ask chat (with MCP tool use) and nameplate vision.

All intelligence is either pre-computed (daily scan) or kept off the latency-critical
path (chat/vision). The hot path only reads facts that are already there.

## Design decisions

- **Hot/cold split**: the only way to guarantee sub-500 ms responses is to keep
  every LLM call off the request path. Latency-critical reads hit DynamoDB;
  intelligence is computed ahead of time.
- **Catalog as hallucination guard**: the model extracts brand and model from a
  nameplate photo, but maintenance schedules come from the static appliance catalog
  — never from the LLM.
- **Coarse-grained, intent-based tools**: each tool returns structured ground truth
  (facts plus a speakable summary) so a conversational front end can render it
  directly.

## The 7 MCP tools

| Tool | What it does |
|---|---|
| `get_home_briefing` | 5-item household briefing: overdue, due-soon, warranty-expiring, recalls |
| `get_appliance` | Full record: brand/model, age, warranty, maintenance history, alerts |
| `add_appliance` | Writes a new appliance from vision-extracted nameplate data |
| `start_photo_onboarding` | Mints a one-time token + QR for phone photo upload |
| `log_maintenance` | Records a completed maintenance task |
| `get_reorder_options` | Consumable reorder options with Amazon links |
| `report_issue` | Issue triage: warranty check + knowledge-base note + recommendation |

## Demo flows

1. **☀️ Morning Briefing** — one MCP call, five items, <500 ms. Tap any row for the
   appliance detail card.
2. **🔧 Issue Triage** — "the water heater is making a strange noise" → warranty
   status, last maintenance, knowledge-base note, and a rule-based recommendation.
3. **📷 Photo Onboarding** — QR code → phone photographs the nameplate → Bedrock
   vision extracts brand/model/serial/manufacture-date with confidence scores →
   pick a category → `add_appliance` writes it to DynamoDB. Verified end-to-end
   with a real Sharp microwave nameplate (R-209KK, all fields ≥0.95 confidence).
4. **Free ask** — Bedrock Claude calls the same MCP tools; answers from real records,
   never invented.

## AWS services

| Service | Responsibility |
|---|---|
| Lambda (4 functions) | MCP server, chat/vision, presigned-URL issuer, daily scan |
| API Gateway (HTTP API) | Browser-facing routes `/mcp*`, `/chat*`, `/presign*` with CORS |
| DynamoDB (single table, `pk`/`sk`) | All state: appliances, tasks, upload tokens, recall cache |
| Bedrock (Claude Sonnet 4.5) | Free-ask chat with tool use; nameplate vision extraction |
| EventBridge Scheduler | Daily CPSC recall scan, 6:00 AM America/Chicago |
| S3 + CloudFront | Static simulator + phone upload page; photo storage (7-day lifecycle) |

> Note: Lambda Function URLs returned account-wide 403s on this account
> (`AccessDeniedException` with `AuthType: NONE`), so all browser traffic goes
> through API Gateway. See `FRICTION_LOG.md`.

## Setup & run

```bash
pip install -r requirements.txt
python src/server.py          # serves MCP streamable HTTP on :8000

# seed the demo household (dates relative to DEMO_CLOCK=2026-10-04)
python src/seed.py --dry-run  # preview without writing
python src/seed.py            # write to DynamoDB (AWS_PROFILE=alexa-ai-user)
```

Deploy notes (Lambda zips, API Gateway routes, EventBridge schedule):
[`deploy/README.md`](deploy/README.md)

## Project layout

```
src/        # MCP server, store, db, seed, catalog, scheduling
web/        # Echo Show simulator (index.html), upload page, privacy/terms
deploy/     # Lambda handlers (mcp, chat, presign, daily-scan) + deploy docs
docs/       # demo video script (pitch), product feedback, design notes
tests/      # unit tests
```

## Product feedback

Honest, per-tool feedback for the hackathon's required Product Feedback section —
what worked, what broke, and what we'd change:
[`docs/product-feedback.md`](docs/product-feedback.md) (source: [`FRICTION_LOG.md`](FRICTION_LOG.md))

## License

MIT — see [LICENSE](LICENSE).

---
Built for *Build, Ship, Shape: Amazon Developer Hackathon* — Alexa+ track.
