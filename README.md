# HomeKeeper

*Every appliance remembered. Every task on time.*

HomeKeeper is a home appliance butler running on Alexa+. It remembers every
appliance's model, maintenance schedule, warranty, and recall information —
ask once and know exactly what to do, and get it done on the spot.

## Architecture

The core decision is a **hot path / cold path split**, driven by Alexa+'s
requirement that MCP round trips stay under 500 ms:

- **Hot path** (every user utterance): Alexa+ (Echo Show or web simulator) →
  MCP Server (Lambda + Function URL, FastMCP stateless) → DynamoDB single
  table. No LLM calls on this path; target p95 < 300 ms.
- **Cold path** (async): phone upload page (S3) or EventBridge daily trigger →
  AgentCore Runtime (Strands agent) → Bedrock Claude + CPSC recall data →
  writes results back to DynamoDB.

All AI work happens in the cold path and is pre-computed; the hot path only
reads facts that are already there.

## Design Decisions

- **Hot/cold path split**: the only way to guarantee sub-500 ms responses is to
  keep every LLM call off the request path. Latency-critical reads hit
  DynamoDB; intelligence is computed ahead of time.
- **Catalog as hallucination guard**: the model extracts brand and model from a
  nameplate photo, but "how often to maintain it" always comes from the static
  appliance catalog — never from the LLM.
- **Alexa+ owns the conversation**: HomeKeeper returns structured ground truth
  (facts plus a speakable summary); Alexa+ handles NLU, phrasing, and UI
  rendering. Tools are coarse-grained and intent-based so Alexa+ picks the
  right one.

## AWS Services

| Service | Responsibility |
|---|---|
| Lambda + Function URL (+ Lambda Web Adapter) | Hosts the FastMCP server (hot path), provisioned concurrency to kill cold starts |
| DynamoDB (single table) | All user state and pre-computed results (`USER#` partition keys) |
| S3 + CloudFront | Hosts the phone upload page; stores nameplate photos |
| AgentCore Runtime + Strands SDK | Runs the Home Analyst agent (cold path): onboarding, scans, briefings |
| Bedrock (Claude) | Vision extraction of nameplates; structured reasoning |
| EventBridge Scheduler | Daily full-home scan trigger |
| SES (optional) | Weekly digest email (cut if time is short) |

## Setup & Run

```bash
cp .env.example .env          # fill in HOMEKEEPER_TABLE, DEMO_CLOCK, AWS_REGION
pip install -r requirements.txt
python src/server.py          # serves streamable HTTP on :8000

# seed the demo household (dates relative to DEMO_CLOCK)
python src/seed.py --dry-run  # preview without writing
python src/seed.py            # write to DynamoDB

# Phase 0: expose locally and deploy the add-on
# 1. cloudflared tunnel --url http://localhost:8000
# 2. alexa-ai new mcp --name "HomeKeeper" --mcp-server-url <tunnel-url>  (Path A: Add-on Agent Skill recommended)
# 3. Fill addon-package/addon.json (replace https://example.com placeholders
#    with real privacy/terms URLs, e.g. GitHub Pages), then alexa-ai deploy
# 4. Test in the Alexa+ web simulator
```

Note: Alexa+ refreshes tool metadata only on `alexa-ai deploy`, so every tool
description change needs a redeploy before testing.

## Test Results

| Tool | p50 | p95 | n |
|---|---|---|---|
| _TBD — Phase 1 latency harness_ | — | — | — |

Target: p95 < 300 ms per tool (Alexa+ requires < 500 ms round trip).

Nameplate OCR accuracy: _TBD — to be measured in Phase 2 with 5–6 real
nameplate photos and reported honestly here._

Dialogue tests: _TBD — 20 scripted utterances in Phase 3._

## Roadmap

- Native checkout via Amazon Pay for consumable reorders (today: purchase handoff links)
- Multi-user auth (OAuth 2.1 + PKCE) — data model is multi-tenant from day one
- Repair-service lead referral for out-of-warranty issues
- Advanced subscription: multi-home, family sharing, warranty document archive

## License

MIT — see [LICENSE](LICENSE).
